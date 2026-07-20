# Plano 63 — Cards colapsáveis no chat: transcrição privada e ferramenta IA minimizadas por padrão

> **Status:** IMPLEMENTADO (F1–F5 + código da F6; itens de verificação visual/scroll da F6 pendentes de teste no browser) · **Data:** 2026-07-20 · **Escopo:** pequeno-médio (frontend-only)
>
> **Origem:** pedido do usuário — "gere um plano para ser possível minimizar/maximizar transcrições privadas da IA e qualquer outra mensagem privada. As transcrições devem vir minimizadas por padrão. Chamadas de tools da IA também devem vir minimizadas com a possibilidade de maximizar."
> **Método:** leitura do código real com `arquivo:linha` (dois sub-agentes em paralelo — render do chat e roles no backend), `grep`/`wc -l` para medir, e 4 perguntas de escopo respondidas pelo usuário (§0) antes de escrever o plano.
>
> **O que está sendo feito e por quê:** um card de `transcription` (descrição de imagem feita pela IA) ocupa hoje **a tela inteira** do fio da conversa — o print que originou o pedido mostra um card de ~25 linhas empurrando a mensagem real para fora da viewport. O mesmo vale para o trace de `tool_call`. Ambos são **ruído de diagnóstico**, não conteúdo de atendimento: o operador quer saber *que existiu* uma transcrição e poder abrir quando precisar. A solução é um **chip de 1 linha com prévia truncada**, clicável para expandir — **zero backend, zero migration, zero config**.
>
> **Perguntas em aberto:** nenhuma. P1/P2/P3 foram respondidas pelo usuário em 2026-07-20 (ver §6 e D6/D7) — o plano está **pronto para execução sem novas decisões**.
>
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| **D1** | Colapsam por padrão **apenas** `transcription` e `tool_call` ✅ (2026-07-20) | `private_note` (nota do operador), `system`, `system_notice`, `error` e `conversation_event` ficam **exatamente como estão**. O gate é `isCollapsibleRole(role)` — nunca `isSystemCardRole`. Nota privada é conteúdo humano deliberado; colapsá-la atrapalharia quem a escreveu. |
| **D2** | **Nenhuma persistência** — "isso é necessário somente na conversa que o usuário abriu. Se ele foi para outra conversa e voltou, não tem problema ficar colapsado" ✅ (2026-07-20) | **Não** cria `ConfigKey`, **não** usa `localStorage`, **não** cria tabela/coluna. Estado vive em memória no container do chat e é **resetado ao trocar de conversa**. Consequência boa: nada a validar em `PUT /api/config`, nenhuma migration, nenhuma chave nova em `web/static/js` para o time de suporte conhecer. |
| **D3** | Colapsado = **chip de 1 linha com prévia truncada** ✅ (2026-07-20) | `🔒 Transcrição privada · A imagem mostra a…  ▸`. O operador bate o olho e decide se abre. Descartadas: chip só com rótulo (exige abrir para saber do que se trata) e clamp de N linhas + "ver mais" (continua ocupando espaço). |
| **D4** | **Nada de controle geral** — só o clique por card ✅ (2026-07-20) | Sem botão "expandir tudo" no header, sem filtro "ocultar transcrições". Menor superfície; o padrão colapsado já resolve a poluição visual. Se um dia quiser, o precedente pronto é o `expandSignal` de [Executions.js:404](../web/static/js/components/Executions.js#L404). |
| **D5** | Princípio fixo: **feature de UI pura** | Nenhuma mudança de contrato de API, de payload WS ou de schema. Um `git revert` do commit desfaz 100% da feature. Isso mantém o risco baixo mesmo com a instância de produção (Empresa Exemplo) rodando `developer`. |
| **D6** | Colapso vale para transcrição de **áudio E de imagem** — "quero que funcione nos dois" ✅ (2026-07-20) | Já sai de graça: as duas gravam com o mesmo role `transcription` (§2.2). Rótulo genérico, sem distinguir a origem. Resolve **P1 = (a)**; nenhuma fase muda. |
| **D7** | Sem âncora de scroll no toggle; guard do "carregar anteriores" só se mexe **com medição** ✅ (2026-07-20) | Resolve **P2 = (a) aceitar** e **P3 = medir na F6 antes de decidir**. A F6 item 4 documenta o deslocamento em vez de corrigi-lo; o guard de [useInfiniteScroll.js:132](../web/static/js/hooks/useInfiniteScroll.js#L132) é intocável sem nova aprovação. |

---

## 1. Resumo executivo

Os 7 roles "painel-only" do chat são renderizados por [SystemMessageCard.js](../web/static/js/components/contacts/SystemMessageCard.js) (193 linhas), uma cadeia de `if (role === …) return html\`…\`` com markup **hardcoded e duplicado** em cada branch. Dois desses cards — `transcription` ([:68-84](../web/static/js/components/contacts/SystemMessageCard.js#L68)) e `tool_call` ([:104-120](../web/static/js/components/contacts/SystemMessageCard.js#L104)) — renderizam o `content` inteiro sem nenhum limite de altura, e o `content` deles é longo por natureza (uma descrição de imagem do LLM passa de 1.500 caracteres).

A solução tem 3 peças, nesta ordem de dependência:

1. **Helpers puros** em [messageView.js](../web/static/js/services/messageView.js) — `isCollapsibleRole()`, `collapsedPreview()` e `cardStateKey()`, cobertos por `node --test` no arquivo de teste que já existe ([messageView.test.js](../web/static/js/services/messageView.test.js)).
2. **Render colapsado** no `SystemMessageCard`, que passa a ser um componente **controlado** (recebe `collapsed` + `onToggleCollapse` como props) — mesmo padrão do `Row` de [AuditLog.js:104](../web/static/js/components/AuditLog.js#L104).
3. **Estado no container** ([ContactDetail.js](../web/static/js/components/contacts/ContactDetail.js)): um `Set` de ids expandidos, chaveado por identidade de mensagem (`_id`/`msg_id`), **nunca por índice**, resetado quando a conversa muda.

⚠️ O ponto que decide se a implementação fica correta ou sutilmente quebrada está no item 3, e é explicado em §2.3: as keys da lista são **índices** e a conversa faz **prepend** de histórico. Um `useState` dentro do card colaria o estado de expansão na mensagem errada depois de "carregar anteriores".

---

## 2. Como funciona hoje (mapa)

### 2.1 — O dispatch de render (fork binário, não switch)

| Peça | Onde | Comportamento |
|------|------|---------------|
| Loop da lista | [ContactDetail.js:419](../web/static/js/components/contacts/ContactDetail.js#L419) | `messages.map((m, i) => …)` |
| Separador de data | [ContactDetail.js:420-429](../web/static/js/components/contacts/ContactDetail.js#L420) | chip centralizado; retornado como `[dateSeparator, card]` (array de 2 vnodes por item) |
| **O fork** | [ContactDetail.js:431-440](../web/static/js/components/contacts/ContactDetail.js#L431) | `if (isSystemCardRole(m.role))` → `<SystemMessageCard>`; senão → `<MessageBubble>` |
| ⚠️ Key da lista | [ContactDetail.js:436](../web/static/js/components/contacts/ContactDetail.js#L436) | `const cardKey = m.role === 'private_note' ? (m._localId \|\| i) : i;` — **índice** para todos os outros cards |
| Predicado | [messageView.js:80-82](../web/static/js/services/messageView.js#L80) | `isSystemCardRole` = `hasOwnProperty` sobre as chaves de `SYSTEM_CARD_VARIANTS` |
| Formatação do texto | [ContactDetail.js:226-231](../web/static/js/components/contacts/ContactDetail.js#L226) | `fmt` = `formatWhatsApp` com nomes de membros do grupo; usado via `dangerouslySetInnerHTML` em **todos** os corpos de card |

### 2.2 — Os dois cards do escopo

| role | linhas | estrutura atual | cor |
|---|---|---|---|
| `transcription` | [SystemMessageCard.js:68-84](../web/static/js/components/contacts/SystemMessageCard.js#L68) | `flex justify-center` → card `max-w-[75%]`; header "Transcrição privada" + cadeado ([:73-76](../web/static/js/components/contacts/SystemMessageCard.js#L73)); corpo `dangerouslySetInnerHTML` ([:77](../web/static/js/components/contacts/SystemMessageCard.js#L77)); hora `float-right` ([:78-80](../web/static/js/components/contacts/SystemMessageCard.js#L78)) | inline `background:#2d1b4e; color:#d4bfff; border:1px solid #4a2d7a` ([:72](../web/static/js/components/contacts/SystemMessageCard.js#L72)) |
| `tool_call` | [SystemMessageCard.js:104-120](../web/static/js/components/contacts/SystemMessageCard.js#L104) | idem, header dinâmico `Ferramenta IA - ${m.agent_name}` quando `showAgentName` ([:111](../web/static/js/components/contacts/SystemMessageCard.js#L111)) | inline `background:#2d1b0e; color:#fbbf24; border:1px solid #78350f` ([:108](../web/static/js/components/contacts/SystemMessageCard.js#L108)) |

**Formato do `content` (medido no backend):**

- `transcription` — string **crua** do LLM, **sem prefixo**. Gravada em [messaging_service.py:661](../app/services/messaging_service.py#L661) (áudio, target=`private`), [:1057](../app/services/messaging_service.py#L1057) (imagem/documento inbound), [:645](../app/services/messaging_service.py#L645) (áudio do operador), [contacts.py:1496-1497](../server/routes/contacts.py#L1496) (`/private-audio`) e [sandbox.py:222,284,351](../server/routes/sandbox.py#L222). O prefixo PT-BR (`_MEDIA_PREFIX` em [transcription.py:22-26](../server/transcription.py#L22)) é aplicado à mensagem `role="user"`, **não** ao card. ⇒ **a prévia precisa truncar o texto livre**.
- `tool_call` — multi-linha, montado em [messaging_service.py:493-502](../app/services/messaging_service.py#L493):
  ```
  🔧 <tool_name>
  <arg_key>: <arg_value>
  → <result>
  ```
  ⇒ **a primeira linha (`🔧 nome_da_tool`) já é o resumo perfeito** — a prévia deve usá-la inteira, não truncar em 70 chars.

### 2.3 — ⚠️ Gotchas de scroll e reconciliação (o que torna o desenho obrigatório)

| # | Fato verificado | Consequência para esta feature |
|---|---|---|
| **G1** | Keys da lista são **índices** ([ContactDetail.js:436](../web/static/js/components/contacts/ContactDetail.js#L436)) e o histórico faz **prepend** ([useConversationSelection.js:301](../web/static/js/components/contacts/hooks/useConversationSelection.js#L301)) | Um prepend de N mensagens remapeia todos os índices. **Um `useState` dentro do `SystemMessageCard` ficaria colado na mensagem errada** depois de "carregar anteriores". ⇒ o card **tem que ser controlado** (stateless) e o estado morar no container, chaveado por `_id`/`msg_id`. Com o card stateless, a key por índice vira inofensiva (a key só afeta reuso de DOM; props corretas ⇒ render correto). |
| **G2** | Não há virtualização; o auto-scroll ao fim roda só com dep `[messages]` ([ContactDetail.js:190-209](../web/static/js/components/contacts/ContactDetail.js#L190), bottom-scroll em [:208](../web/static/js/components/contacts/ContactDetail.js#L208)) | Expandir/colapsar **não** re-executa o efeito. Isso é o comportamento **desejado** (a viewport não deve saltar quando o usuário clica), mas significa que o crescimento do card empurra o conteúdo abaixo dele. Ver P2. |
| **G3** | **Zero `ResizeObserver`** em todo `web/static/js` (grep confirmado) | Nada corrige a posição de scroll quando a altura de um item muda. É aceitável para um clique deliberado do usuário; **não** seria se houvesse expansão automática em massa. |
| **G4** | Guard anti-cascata: `if (el.scrollHeight - el.clientHeight <= 4) return;` ([useInfiniteScroll.js:132](../web/static/js/hooks/useInfiniteScroll.js#L132)) | **Colapsar por padrão ENCOLHE o conteúdo.** Numa conversa curta dominada por transcrições, a lista pode passar a caber inteira na viewport → "carregar anteriores" para de disparar até o usuário rolar. Risco real, testável (F6). |
| **G5** | O default **não pode** ser aplicado via `useEffect` | Um `setState` em efeito causa segundo render **depois** do bottom-scroll de [:208](../web/static/js/components/contacts/ContactDetail.js#L208) → salto visível ao abrir a conversa. ⇒ o default é **derivado no próprio render**: "ausente do `Set` ⇒ colapsado". |
| **G6** | `_id` do `tool_call` no WS é **condicional**: [messaging_service.py:531-532](../app/services/messaging_service.py#L531) só o inclui `if saved and saved.get("id")` | A chave de estado precisa de fallback: `_id` → `msg_id` → `role:ts`. |
| **G7** | `data-mid=${m._id}` (âncora do deep-link/busca) existe em `transcription` ([:70](../web/static/js/components/contacts/SystemMessageCard.js#L70)) mas **NÃO** em `tool_call` ([:106](../web/static/js/components/contacts/SystemMessageCard.js#L106)) | `focusMessage` ([ContactDetail.js:186](../web/static/js/components/contacts/ContactDetail.js#L186)) não alcança um `tool_call`. Correção barata junto (F5). |
| **G8** | O corpo usa `dangerouslySetInnerHTML={{__html: fmt(m.content)}}` | **NUNCA truncar a saída de `fmt()`** — cortaria uma tag no meio e injetaria HTML quebrado. A prévia sai do `content` **cru**, é truncada como texto e renderizada por **interpolação normal do htm** (que escapa). Ver §5 R1. |

### 2.4 — Padrões de expandir/colapsar que já existem no repo

Não existe **nenhum `<details>`** no frontend nem nada colapsável no chat. Fora do chat, há três precedentes — o mais próximo é o segundo:

| Padrão | Onde | Serve como |
|---|---|---|
| `Collapsible` com `expandSignal` global | [Executions.js:259-272](../web/static/js/components/Executions.js#L259), botão em [:463-464](../web/static/js/components/Executions.js#L463) | Referência de chevron `▾/▸` ([:268](../web/static/js/components/Executions.js#L268)) e de "expandir tudo" **se D4 for reaberto no futuro**. Notável: já é aplicado a dados de tool call, só que na tela de Execuções. |
| **Linha controlada, estado no container por id** | [AuditLog.js:197](../web/static/js/components/AuditLog.js#L197) (`expandedId` no pai), [:104](../web/static/js/components/AuditLog.js#L104) (`Row({row, expanded, onToggle})` puro), [:458-459](../web/static/js/components/AuditLog.js#L458) | **O padrão a copiar.** Estado no pai chaveado por `row.id`, componente filho puro/controlado. É exatamente o que G1 exige. |
| Clamp CSS + modal | [PluginsManager.js:310-322](../web/static/js/components/PluginsManager.js#L310) (`-webkit-line-clamp:3` + "ver mais") | Descartado por D3. |

---

## 3. Inventário / análise

### 3.1 — Itens a fazer

| # | Item | Arquivo:linha | O que falta | Abordagem | Risco | Esforço |
|---|------|---------------|-------------|-----------|-------|---------|
| I1 | Marcar quais roles colapsam | [messageView.js:42-52](../web/static/js/services/messageView.js#L42) | nada declara "colapsável" | `collapsible: true` em `transcription` + `tool_call`; `isCollapsibleRole()` exportado | Baixo | S |
| I2 | Prévia truncada pura | **novo** em [messageView.js](../web/static/js/services/messageView.js) | não existe | `collapsedPreview(role, content, {maxLen})` — 1ª linha para `tool_call`, colapso de whitespace + corte em fronteira de palavra + `…` para o resto. `node --test` | Baixo | S |
| I3 | Chave estável de estado | **novo** em [messageView.js](../web/static/js/services/messageView.js) | keys são índices (G1) | `cardStateKey(m, index)` → `_id` → `msg_id` → `role:ts` → índice (degradado, documentado) | Baixo | S |
| I4 | Render colapsado | [SystemMessageCard.js:68-84](../web/static/js/components/contacts/SystemMessageCard.js#L68) e [:104-120](../web/static/js/components/contacts/SystemMessageCard.js#L104) | card sempre renderiza o `content` inteiro | Novas props `collapsed`/`onToggleCollapse`; quando `collapsed`, chip de 1 linha reusando **o mesmo `style` inline** do card expandido | Médio | M |
| I5 | Estado no container + reset por conversa | [ContactDetail.js:431-440](../web/static/js/components/contacts/ContactDetail.js#L431) | não existe | `Set` de chaves expandidas + `toggle` estável (`useCallback`); reset com dep na conversa | Médio | M |
| I6 | Deep-link/busca abre o card | [ContactDetail.js:186](../web/static/js/components/contacts/ContactDetail.js#L186), G7 | `focusMessage` rolaria até um chip fechado; `tool_call` sem `data-mid` | Expandir a chave-alvo antes de focar + adicionar `data-mid` em `tool_call` | Baixo | S |
| I7 | A11y + tema | I4 | chip novo é interativo | `role="button"`, `tabIndex=0`, `aria-expanded`, Enter/Espaço, `title` com o texto completo; conferir os 2 temas | Baixo | S |

### 3.2 — Falsos positivos descartados

| Item | Por que **não** é problema |
|------|----------------------------|
| **`SYSTEM_CARD_VARIANTS` "já é data-driven"** | ⚠️ **Armadilha real.** O comentário em [messageView.js:6](../web/static/js/services/messageView.js#L6) diz "drive the data-driven SystemMessageCard", mas a tabela ([:35-77](../web/static/js/services/messageView.js#L35)) é **dado morto**: o único consumidor é `isSystemCardRole` ([:81](../web/static/js/services/messageView.js#L81)) + o próprio teste. `SystemMessageCard` importa **só** `isSystemCardRole` ([SystemMessageCard.js:4](../web/static/js/components/contacts/SystemMessageCard.js#L4)) e tem markup hardcoded. **Adicionar `collapsible: true` na tabela NÃO muda o render sozinho** — a F3 obriga o card a ler a tabela. Sem isso, o executor "termina" a F1, vê o teste verde e nada acontece na tela. |
| Backend precisa mudar | Não. Nenhuma coluna, endpoint ou payload muda. O serializador [`_row_to_dict`](../db/repositories/message_repo.py#L531) já entrega `role`, `content`, `ts`, `_id` — é tudo que a feature consome. |
| Precisa de `ConfigKey` nova | Não, por D2. (Se um dia precisar, o caminho é 1 linha em [settings.py:105+](../config/settings.py#L105) — `writable_config_keys()` em [:253-255](../config/settings.py#L253) deriva o resto. Registrado aqui só para não re-investigar.) |
| Precisa de preferência por usuário | Não, por D2 — e **não existe infraestrutura**: a tabela `users` ([tables.py:316-332](../db/tables.py#L316)) não tem coluna de preferências, e nenhuma das 43 tabelas é `user_settings`. Seria tabela + migration + endpoint. |
| O Sandbox quebra | Não. [Sandbox.js:129](../web/static/js/components/Sandbox.js#L129) e [:142](../web/static/js/components/Sandbox.js#L142) **filtram `tool_call` fora** antes de renderizar. Nada a fazer lá. |
| A key por índice precisa ser corrigida antes | Não é bloqueante **desde que o card seja controlado** (G1). Corrigir a key é hardening opcional (F2), não pré-requisito. |
| `private_note` com mídia (áudio/imagem) | Fora do escopo por D1 — e é o único card com 3 vias de corpo ([SystemMessageCard.js:53-59](../web/static/js/components/contacts/SystemMessageCard.js#L53)), o mais caro de colapsar. Bom que ficou de fora. |
| Plugins que gravam esses roles | `transcription`/`tool_call` são gravados **só pelo core** (os 5 + 1 call sites de §2.2). Plugins usam `system` ([melhorias/logic.py:268](../assets/plugin_examples/melhorias/logic.py#L268)) e `private_note` ([protocolos/logic.py:646](../assets/plugin_examples/protocolos/logic.py#L646)) — ambos fora do escopo. Nenhum plugin quebra. |

---

## 4. Fases / Roadmap

```
WAVE 0   F1 ─ helpers puros + node --test        · F2 ─ key estável (hardening, opcional)
         🟢 ambas em paralelo — arquivos distintos, sem dependência
              │  (barreira: F3 consome os helpers da F1)
              ▼
WAVE 1   F3 ─ render colapsado no card  ──→  F4 ─ estado no container + reset
         🔴 sequenciais: F4 precisa das props que a F3 cria
              │
              ▼
WAVE 2   F5 ─ deep-link/busca auto-expande        · F6 ─ a11y + tema + scroll
         🟢 ambas em paralelo — [depende de: F4]
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|------|------|------------|-------|-------|----------------|
| 0 | **F1** | Helpers puros em `messageView.js` + testes | 🟢 [bloqueia: F3] | Baixo | `node --test web/static/js/services/messageView.test.js` verde com os casos novos |
| 0 | **F2** | Key de lista estável em `ContactDetail.js` (hardening) | 🟢 [independente] | Baixo | Chat renderiza igual; "carregar anteriores" não embaralha cards |
| 1 | **F3** | `SystemMessageCard` controlado + chip colapsado | 🔴 [depende: F1] [bloqueia: F4] | Médio | Passando `collapsed=true` na mão, o card vira chip de 1 linha nos 2 roles |
| 1 | **F4** | Estado no `ContactDetail` + reset por conversa | 🔴 [depende: F3] | Médio | Abrir conversa: transcrições/tools colapsadas; clicar expande; trocar de conversa e voltar → colapsadas de novo |
| 2 | **F5** | Deep-link/busca expande o alvo + `data-mid` em `tool_call` | 🟢 [depende: F4] | Baixo | Clicar num resultado de busca que é transcrição rola até ele **e** o abre |
| 2 | **F6** | A11y, modo escuro e verificação de scroll (G4) | 🟢 [depende: F4] | Baixo | Tab+Enter alternam; legível nos 2 temas; "carregar anteriores" ainda dispara |

> **Paralelização:** a Wave 0 tem duas frentes em arquivos distintos ([messageView.js](../web/static/js/services/messageView.js) × [ContactDetail.js](../web/static/js/components/contacts/ContactDetail.js)) — despache junto. A Wave 1 é **estritamente sequencial** (F4 consome a API de props que a F3 define; fazê-las em paralelo gera conflito no mesmo par de arquivos). A Wave 2 volta a paralelizar: F5 mexe no efeito de scroll/`focusMessage`, F6 no markup do chip — sem sobreposição.
>
> **Disciplina:** um refactor por commit; `node --test` verde a cada fase; nunca avançar com teste vermelho não explicado. Como a feature é 100% de UI (D5), não há caracterização de backend a fazer — o equivalente aqui é a F1 (helpers puros testados **antes** de o render depender deles).

---

### Fase 1 (F1) — Helpers puros em `messageView.js` 🟢

**Objetivo:** toda a lógica decidível (o que colapsa, que texto aparece no chip, qual a identidade da mensagem) vira função pura testável, fora do componente.

**Itens:**

1. `[paralelo]` Em [messageView.js:42-46](../web/static/js/services/messageView.js#L42) (`transcription`) e [:48-52](../web/static/js/services/messageView.js#L48) (`tool_call`): adicionar `collapsible: true`. **Não** adicionar nos outros 5 (D1). Atualizar o JSDoc do `@type` em [:19-33](../web/static/js/services/messageView.js#L19) com o campo novo.
2. `[paralelo]` Exportar `isCollapsibleRole(role)` — `!!(SYSTEM_CARD_VARIANTS[role] && SYSTEM_CARD_VARIANTS[role].collapsible)`. Espelha a forma de `isSystemCardRole` ([:80-82](../web/static/js/services/messageView.js#L80)).
3. `[paralelo]` Exportar `collapsedPreview(role, content, { maxLen = 70 } = {})` → **string de texto puro**:
   - `content` vazio/nulo ⇒ `''`.
   - `role === 'tool_call'` ⇒ a **primeira linha não-vazia** (já é `🔧 <tool_name>`, ver §2.2); se ela passar de `maxLen`, truncar como abaixo.
   - demais ⇒ colapsar `\s+` em espaço único, `trim()`, e se `length > maxLen` cortar na **última fronteira de palavra** antes de `maxLen` (fallback: corte duro) + `'…'`.
   - ⚠️ Recebe o `content` **cru**; nunca a saída de `fmt()` (G8/R1).
4. `[paralelo]` Exportar `cardStateKey(m, index)` → `string`, na ordem: `m._id != null` ⇒ `` `id:${m._id}` ``; senão `m.msg_id` ⇒ `` `mid:${m.msg_id}` ``; senão `` `rt:${m.role}:${m.ts}` ``; senão `` `ix:${index}` `` (degradado — comentar que só ocorre em mensagem sem id/ts, e que nesse caso a expansão pode se deslocar após prepend). Justificativa do fallback em G6.
5. `[sequencial]` Casos novos em [messageView.test.js](../web/static/js/services/messageView.test.js) (o arquivo já existe, 12 testes): `isCollapsibleRole` true só para os 2 roles e false para os outros 5 + `user`/`assistant`/`undefined`; `collapsedPreview` para tool_call multi-linha (pega só a 1ª), para texto longo (corta em palavra + `…`), para texto curto (passa intacto, **sem** `…`), para vazio/nulo; `cardStateKey` na precedência `_id > msg_id > role:ts > índice`.

**Pronto quando:** `node --test web/static/js/services/messageView.test.js` verde, incluindo os testes antigos ([:21-38](../web/static/js/services/messageView.test.js#L21) valida a tabela — confirme que `collapsible` não os quebrou). Nada mudou visualmente no chat (esperado: a tabela é dado morto até a F3 — §3.2).

#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-07-20)
- **O que foi feito:** [messageView.js](../web/static/js/services/messageView.js) — `collapsible: true` em `transcription` e `tool_call` (+ `collapsible?: boolean` no JSDoc `@type`); novas funções puras exportadas `isCollapsibleRole(role)`, `collapsedPreview(role, content, {maxLen=70})` e `cardStateKey(m, index)`. [messageView.test.js](../web/static/js/services/messageView.test.js) — 12 casos novos.
- **Como foi feito / decisões:** `cardStateKey` na precedência exata do plano (`_id` → `msg_id` não-vazio → `role:ts` → `ix:index`); trata `_id: 0` como id válido (`!= null`, não falsy) e `msg_id: ''` cai para `role:ts`. `collapsedPreview` corta na última fronteira de palavra e faz corte-duro para uma única palavra gigante; whitespace colapsado só no ramo não-`tool_call` (o `tool_call` usa a 1ª linha não-vazia inteira).
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --test messageView.test.js` → **20/20 verde** (8 antigos + 12 novos). Suíte inteira de `web/static/js/**/*.test.js` → **231/231 verde**.

---

### Fase 2 (F2) — Key de lista estável (hardening opcional) 🟢

**Objetivo:** parar de usar índice como key de reconciliação dos cards, para que o prepend de histórico reuse o DOM certo. **Não é pré-requisito** da feature (G1: o card controlado já é imune), mas remove uma classe inteira de bug futuro.

**Itens:**

1. `[sequencial]` Em [ContactDetail.js:436](../web/static/js/components/contacts/ContactDetail.js#L436), trocar `cardKey` por `cardStateKey(m, i)` (F1), preservando a precedência de `_localId` para `private_note` (mensagem otimista ainda não salva — [useComposer.js:173](../web/static/js/components/contacts/hooks/useComposer.js#L173)): `m._localId || cardStateKey(m, i)`.
2. `[sequencial]` Avaliar o mesmo para `MessageBubble` em [ContactDetail.js:443](../web/static/js/components/contacts/ContactDetail.js#L443) (`m._localId || i`). ⚠️ **Mais arriscado** — o bubble tem estado visual (highlight de foco, seleção em lote via [:449](../web/static/js/components/contacts/ContactDetail.js#L449)). Se houver qualquer dúvida, **deixe como está** e registre no Status de execução; não é escopo desta feature.
3. `[sequencial]` Remover as `key=${…}` **internas** de [SystemMessageCard.js:35,70,127,148](../web/static/js/components/contacts/SystemMessageCard.js#L35) — são inúteis (a key precisa estar no vnode membro do array, como explica o comentário em [ContactDetail.js:432-435](../web/static/js/components/contacts/ContactDetail.js#L432)) e confundem quem lê.

**Pronto quando:** abrir uma conversa longa, rolar até o topo para disparar "carregar anteriores" 2×, e confirmar que nenhum card trocou de conteúdo/posição e que a viewport não saltou.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-07-20)
- **O que foi feito:** [ContactDetail.js:485-505](../web/static/js/components/contacts/ContactDetail.js#L485) — `cardKey` do fork de system-card passou de índice para `m._localId || cardStateKey(m, i)`. Removidas as `key=${…}` internas **inúteis** de TODOS os 7 branches do [SystemMessageCard.js](../web/static/js/components/contacts/SystemMessageCard.js) (não só os 4 do texto do plano — a mesma justificativa vale para os 7).
- **Como foi feito / decisões:** **Item 2 (MessageBubble): NÃO mexi** — a key do bubble segue `m._localId || i`. O bubble carrega estado visual (highlight de foco, seleção em lote) e o card controlado já é imune ao prepend (G1), então trocar a key do bubble seria risco sem ganho para esta feature. Registrado como decisão consciente.
- **Problemas / pendências:** revisão adversarial apontou (LOW) que o fallback degradado `rt:role:ts` de `cardStateKey` pode colidir para **dois** cards do mesmo role + mesmo `ts` **ambos sem `_id` e sem `msg_id`** (ex.: 2 `tool_call` por WS com save falho no mesmo segundo — G6). É estritamente mais raro que o caminho comum (`id:`/`mid:`, sempre únicos) e é a degradação que o próprio plano documenta; a função está fixada pelo spec + testes, então **mantida como está**. Nos caminhos reais é mais estável que a key-por-índice antiga (não embaralha no "carregar anteriores").
- **Verificação:** `node --check` OK; suíte 231/231 verde; harness de render (preact+htm vendorizados) confirma que os 7 branches renderizam e que remover as keys internas não muda a saída.

---

### Fase 3 (F3) — `SystemMessageCard` controlado + chip colapsado 🔴 [depende de: F1]

**Objetivo:** o card ganha duas props e um segundo modo de render. Continua **stateless** (G1).

**Itens:**

1. `[sequencial]` Assinatura em [SystemMessageCard.js:28](../web/static/js/components/contacts/SystemMessageCard.js#L28) passa a aceitar `collapsed = false` e `onToggleCollapse = null`. Ambas com default ⇒ **quem não passar nada mantém o comportamento atual byte-a-byte** (importante: o card é usado só pelo `ContactDetail`, mas o default protege contra reuso futuro).
2. `[sequencial]` Guard novo no topo do componente, **antes** das branches por role:
   ```js
   if (collapsed && isCollapsibleRole(role)) return html`<${CollapsedCardChip} … />`;
   ```
   O gate é `isCollapsibleRole`, **nunca** `isSystemCardRole` (D1) — mesmo que o container passe `collapsed=true` por engano para outro role, nada acontece.
3. `[sequencial]` Novo componente local `CollapsedCardChip({ message, variant, onToggle, showAgentName })` no mesmo arquivo:
   - Wrapper `flex justify-center mt-[4px]` (idêntico ao expandido, para o espaçamento não pular ao alternar).
   - Card `max-w-[75%]` com o **mesmo `style` inline** de `SYSTEM_CARD_VARIANTS[role].style` ([messageView.js:45](../web/static/js/services/messageView.js#L45) / [:51](../web/static/js/services/messageView.js#L51)) — reusar a string da tabela, **não** re-digitar o hex. É isto que faz a tabela deixar de ser dado morto (§3.2) e o que garante o tema (R6).
   - Conteúdo em **uma linha**: ícone (cadeado/chave, os mesmos SVGs de [:74](../web/static/js/components/contacts/SystemMessageCard.js#L74) e [:110](../web/static/js/components/contacts/SystemMessageCard.js#L110)) · rótulo (`variant.label`, com o sufixo `- ${m.agent_name}` de [:111](../web/static/js/components/contacts/SystemMessageCard.js#L111) quando `showAgentName`) · `·` · **prévia** · chevron `▸`.
   - Layout: container `flex items-center gap-1` + a prévia em `truncate min-w-0` (um só `overflow` — nunca quebra em 2 linhas), rótulo e chevron em `shrink-0`.
   - ⚠️ **A prévia é interpolada como texto** (`${collapsedPreview(role, m.content)}`) — **nunca** `dangerouslySetInnerHTML` (R1). Perde-se a formatação WhatsApp na prévia; é o comportamento correto e desejado.
   - `onClick=${onToggle}` no card inteiro + `cursor-pointer` (P5).
4. `[sequencial]` No modo **expandido** dos dois roles, acrescentar o affordance de fechar: o header ([:73-76](../web/static/js/components/contacts/SystemMessageCard.js#L73) e [:109-112](../web/static/js/components/contacts/SystemMessageCard.js#L109)) ganha `onClick=${onToggle}`, `cursor-pointer` e um chevron `▾` à direita. **Só** quando `onToggleCollapse` foi passada — sem ela, header inerte como hoje.
5. `[sequencial]` Adicionar `data-mid=${m._id}` no chip **e** no card expandido de `tool_call` ([:106](../web/static/js/components/contacts/SystemMessageCard.js#L106)) — hoje falta (G7). O de `transcription` já tem ([:70](../web/static/js/components/contacts/SystemMessageCard.js#L70)); replicá-lo no chip.

**Pronto quando:** com um `collapsed=true` fixo no código (temporário), abrir uma conversa com transcrição e ver o chip de 1 linha com a prévia certa; trocar para `false` e ver o card original **idêntico ao de antes** (compare com um print). Reverter o valor fixo antes de commitar.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída (2026-07-20)
- **O que foi feito:** [SystemMessageCard.js](../web/static/js/components/contacts/SystemMessageCard.js) — assinatura ganhou `collapsed = false` e `onToggleCollapse = null`; guard no topo `if (collapsed && isCollapsibleRole(role)) return <CollapsedCardChip …/>`; novo componente local `CollapsedCardChip` (ícone · rótulo · prévia truncada · `▸`, reusando o `style` inline de `SYSTEM_CARD_VARIANTS[role]`); helper `cardIconPath(icon)`; header dos branches `transcription`/`tool_call` virou clicável (`role=button`/`tabIndex`/`aria-expanded="true"`/`onClick`/`onKeyDown` + chevron `▾`) **só quando `onToggleCollapse` é passada**; `data-mid=${m._id}` adicionado ao `tool_call` (G7).
- **Como foi feito / decisões:** gate é `isCollapsibleRole` (nunca `isSystemCardRole`) — D1/R8. Prévia interpolada como **texto** (htm escapa), nunca `dangerouslySetInnerHTML` (R1/G8); `title` carrega o `content` completo. Rótulo do chip = `variant.label` + sufixo `- ${agent_name}` só no `tool_call` (espelha [:111]). Sem `onToggleCollapse`, o card expandido é **byte-equivalente** ao de antes (atributos `null` são omitidos pelo Preact). **P4 = (a)** (sem hora no chip) e **P5 = card inteiro clicável** implementados como recomendado.
- **Problemas / pendências:** revisão a11y/layout achou um bug **MÉDIO** (corrigido nesta fase): o `label` estava `shrink-0` sem `truncate`, então um `agent_name` longo (config do operador) estourava o chip `max-w-[75%]`, empurrava o chevron pra fora e criava scroll horizontal no chat. **Corrigido**: `label`+`·`+prévia vivem num único grupo `truncate min-w-0`; só ícone e chevron são `shrink-0`, e a truncagem corta a prévia primeiro. Separador `·` virou `aria-hidden` (tirei do nome acessível do botão). **Gap conhecido menor (não corrigido, fora do escopo da F6):** ao alternar por teclado o elemento focado é desmontado (chip↔header são nós diferentes) e o foco cai no body — polimento WCAG 2.4.3 para plano futuro.
- **Verificação:** harness com preact+htm vendorizados (11 chamadas de render sem throw) + asserts estruturais (chip tem `role=button`/`aria-expanded=false`/`▸`/`data-mid`/`title`=conteúdo completo; expandido-com-toggle tem `aria-expanded=true`/`▾`; legado-sem-toggle é inerte; `tool_call` chip usa 1ª linha + agent name; overflow-fix: grupo truncável não-`shrink-0`, exatamente 2 `shrink-0`).

---

### Fase 4 (F4) — Estado no container + reset por conversa 🔴 [depende de: F3]

**Objetivo:** ligar a feature de verdade — colapsado por padrão, clique alterna, troca de conversa reseta (D2).

**Itens:**

1. `[sequencial]` No `ContactDetail`, junto dos outros `useState` do container (ex.: [:76](../web/static/js/components/contacts/ContactDetail.js#L76)): `const [expandedCards, setExpandedCards] = useState(() => new Set());`
2. `[sequencial]` `toggleCard` em `useCallback` com deps estáveis — cria um `Set` **novo** a cada toggle (mutar o existente não dispara re-render no Preact).
3. `[sequencial]` Reset ao trocar de conversa (D2): `useEffect(() => setExpandedCards(new Set()), [conversationId, phone, channelId])`. ⚠️ Este é o **único** `setState` em efeito permitido aqui, e é seguro porque roda **antes** do primeiro paint de mensagens da conversa nova. Não confundir com G5 — o **default** continua derivado no render.
4. `[sequencial]` No fork de [ContactDetail.js:431-440](../web/static/js/components/contacts/ContactDetail.js#L431), calcular por item **durante o render** (G5):
   ```js
   const stateKey = cardStateKey(m, i);
   const collapsed = isCollapsibleRole(m.role) && !expandedCards.has(stateKey);
   ```
   e passar `collapsed=${collapsed} onToggleCollapse=${() => toggleCard(stateKey)}` ao `SystemMessageCard`. **Ausente do `Set` ⇒ colapsado** — é isso que entrega "minimizado por padrão" sem nenhum efeito, nenhum flash e nenhum salto de scroll.
5. `[sequencial]` Confirmar que o `Set` não vaza entre conversas quando o `ContactDetail` **não** remonta (o reset do item 3 cobre os dois casos, remontagem ou não — é idempotente).

**Pronto quando:**
- Abrir uma conversa com transcrição de imagem (a do print): o card gigante virou um chip de 1 linha, e a **mensagem real do cliente está visível** na viewport ao abrir.
- Clicar no chip → expande; clicar no header → colapsa.
- Expandir 2 cards, trocar de conversa, voltar → ambos **colapsados** de novo (D2).
- Expandir um card, rolar ao topo e disparar "carregar anteriores" → o card certo continua expandido (G1).
- Chegar uma transcrição **nova** por WS com a conversa aberta → entra colapsada e o chat rola ao fim normalmente.

#### Status de execução — Fase 4
**Estado:** ✅ Concluída (2026-07-20)
- **O que foi feito:** [ContactDetail.js](../web/static/js/components/contacts/ContactDetail.js) — `const [expandedCards, setExpandedCards] = useState(() => new Set())`; `toggleCard = useCallback` (cria um `Set` novo por toggle); `useEffect(() => setExpandedCards(new Set()), [conversationId, phone, channelId])` (reset por conversa, D2); no fork, `collapsed = isCollapsibleRole(m.role) && !expandedCards.has(stateKey)` **derivado no render** (G5) e props `collapsed`/`onToggleCollapse=${() => toggleCard(stateKey)}`. `useCallback` adicionado ao import de `preact/hooks`.
- **Como foi feito / decisões:** o default "colapsado" é derivado no render (ausente do `Set` ⇒ colapsado), **sem nenhum `useEffect`** — sem flash, sem 2º render, sem salto (G5/R3). O reset é declarado **antes** do efeito de scroll `[messages]` para que, ao trocar de conversa, ele limpe primeiro e o re-expand de deep-link (F5) vença. `onToggleCollapse` é passada a todos os system-cards, mas só os 2 colapsáveis a leem (os outros 5 ignoram → render idêntico).
- **Problemas / pendências:** nenhuma. Revisão confirmou: ordem de efeitos correta (sync e async), sem loop de render, card controlado imune a prepend (G1), string em `style` e interpolação parcial de `class` funcionam no Preact/htm.
- **Verificação:** suíte 231/231 verde; harness de render confirma o par colapsado/expandido; validação manual no browser recomendada ao subir (abrir conversa com transcrição de imagem → chip de 1 linha com a mensagem real visível; clicar expande; trocar de conversa e voltar → colapsado de novo).

---

### Fase 5 (F5) — Deep-link/busca expande o card-alvo 🟢 [depende de: F4]

**Objetivo:** um resultado de busca que aponta para uma transcrição não pode levar o operador a um chip fechado sem explicação.

**Itens:**

1. `[paralelo]` Em [ContactDetail.js:198-206](../web/static/js/components/contacts/ContactDetail.js#L198) (o ramo `pendingScrollRef`), antes de chamar `focusMessage(target)`, adicionar a chave correspondente ao `Set` de expandidos — o `target` é um `_id`, e `cardStateKey` prefixa com `id:`, então a chave é derivável direto.
2. `[paralelo]` Verificar que o `focusMessage` ([:170-187](../web/static/js/components/contacts/ContactDetail.js#L170)) ainda encontra o elemento: como a expansão é `setState`, o `data-mid` só existe no card expandido **no render seguinte**. O código já tolera isso — o comentário em [:203-205](../web/static/js/components/contacts/ContactDetail.js#L203) diz que, se o alvo não estiver renderizado, ele espera a próxima atualização de `messages`. ⚠️ **A confirmar na execução:** se a expansão sozinha não re-disparar o efeito (dep é `[messages]`), pode ser preciso incluir `expandedCards` nas deps. Medir antes de mudar.
3. `[paralelo]` Confirmar que o `data-mid` adicionado ao `tool_call` na F3 (item 5) tornou esses cards alcançáveis.

**Pronto quando:** buscar um trecho que só existe dentro de uma transcrição, clicar no resultado → o chat rola até o card, **e ele está aberto e destacado** (`wa-msg-highlight`, [:185](../web/static/js/components/contacts/ContactDetail.js#L185)).

#### Status de execução — Fase 5
**Estado:** ✅ Concluída (2026-07-20)
- **O que foi feito:** [ContactDetail.js](../web/static/js/components/contacts/ContactDetail.js) — no ramo `pendingScrollRef` do efeito `[messages]`, se o alvo é um card colapsável ainda colapsado, ele é expandido (`setExpandedCards(add id:target)`) antes do `focusMessage`. Novo `focusAfterExpandRef` + novo `useEffect(…, [expandedCards])` que **re-foca** o alvo depois que o card expande. `data-mid` no `tool_call` (F3) tornou esses cards alcançáveis pelo `focusMessage`.
- **Como foi feito / decisões:** **Resultado da medição do item 2 (o ponto crítico da F5):** ao expandir, o `SystemMessageCard` troca o **tipo do vnode** (colapsado = componente `CollapsedCardChip`; expandido = `<div>`), então o Preact **substitui o nó do DOM** — o `wa-msg-highlight` aplicado ao chip **não sobrevive**. Naïvemente incluir `expandedCards` nas deps do efeito `[messages]` quebraria o bottom-scroll em todo toggle manual (G2). Solução: um **efeito dedicado `[expandedCards]`** guardado por `focusAfterExpandRef` — re-foca o card **já expandido** após o commit; em toggle manual o ref é `null` ⇒ no-op (não rola, G2). Só expande alvos **genuinamente colapsáveis** (não polui o `Set` com key de bubble normal).
- **Problemas / pendências:** nenhuma. (O foco de teclado não é restaurado ao card destino — mesma limitação a11y menor registrada na F3; deep-link restaura *scroll* + *highlight*, não *foco* do teclado.)
- **Verificação:** revisão de correção Preact confirmou o timing (efeito roda após o commit da expansão, sem double-fire, ref limpo no 1º sucesso). Validação manual recomendada: buscar trecho que só existe numa transcrição → clicar no resultado → rola até o card **aberto e destacado**.

---

### Fase 6 (F6) — A11y, modo escuro e verificação de scroll 🟢 [depende de: F4]

**Objetivo:** fechar as arestas que a regra de tema do [CLAUDE.md](../CLAUDE.md) e o gotcha G4 exigem.

**Itens:**

1. `[paralelo]` A11y do chip (F3): `role="button"`, `tabIndex="0"`, `aria-expanded="false"` (e `"true"` no header expandido), `onKeyDown` para Enter/Espaço, `title` com o `content` completo (tooltip nativo = ler sem abrir).
2. `[paralelo]` **Modo escuro** (regra obrigatória do CLAUDE.md): abrir o chat, ligar o tema escuro pelo menu da engrenagem e conferir o contraste dos dois chips. Como eles reusam os `style` inline da tabela ([messageView.js:45](../web/static/js/services/messageView.js#L45), [:51](../web/static/js/services/messageView.js#L51)) — hex fixos que **não** são cobertos pelos overrides `html.dark` do [custom.css](../web/static/css/custom.css) — o resultado deve ser idêntico ao card expandido de hoje nos dois temas. Se algum elemento novo (chevron, separador `·`) usar cor crua, trocar por `currentColor`/`opacity`.
3. `[paralelo]` **Verificar G4:** abrir uma conversa **curta** dominada por transcrições, cujo conteúdo colapsado caiba inteiro na viewport, e confirmar se "carregar anteriores" ainda dispara. O guard é [useInfiniteScroll.js:132](../web/static/js/hooks/useInfiniteScroll.js#L132) (`scrollHeight - clientHeight <= 4`). Se travar: registrar no Status de execução e tratar como **P3** (não corrigir por impulso — mexer no guard tem histórico de regressão, ver o comentário em [ContactDetail.js:60-63](../web/static/js/components/contacts/ContactDetail.js#L60)).
4. `[paralelo]` Conferir o comportamento de scroll ao expandir um card **acima** da viewport (G2/G3): documentar o que acontece. Por P2, o esperado é "aceita-se o deslocamento"; se estiver ruim demais na prática, virar item de plano futuro, não escopo desta fase.

**Pronto quando:** Tab chega ao chip e Enter alterna; os dois temas legíveis; o resultado do teste do item 3 registrado (verde **ou** com a pendência descrita).

#### Status de execução — Fase 6
**Estado:** ✅ Concluída (código); itens de verificação visual/scroll pendentes de teste no browser
- **O que foi feito:** a11y do chip e do header (item 1) foi implementada junto da F3 — `role="button"`, `tabIndex="0"`, `aria-expanded` (`false` no chip, `true` no header expandido), `onKeyDown` Enter/Espaço com `preventDefault` (não rola a página no Espaço), `title` com o conteúdo completo, chevrons `▸`/`▾` `aria-hidden`. Separador `·` marcado `aria-hidden` (revisão a11y).
- **Como foi feito / decisões (item 2 — modo escuro):** o chip **reusa a string `style` inline da tabela** (`#2d1b4e`/`#2d1b0e` etc.) — hex de intenção idênticos ao card expandido de hoje, **não** cobertos (nem afetados) pelos overrides `html.dark`, logo legíveis nos 2 temas por construção. Glyphs novos (`·`, `▸`, `▾`) usam `currentColor` + `opacity` (sem cor crua). Revisão a11y confirmou conformidade com a regra do CLAUDE.md e que não há elemento novo ilegível em nenhum tema.
- **Problemas / pendências (itens 3 e 4 — G4/G2, exigem browser):**
  - **Item 3 (G4 — "carregar anteriores" pode travar quando o colapso encolhe a lista):** **NÃO medido ainda** (exige rodar o app com uma conversa curta dominada por transcrições). O guard [useInfiniteScroll.js:132](../web/static/js/hooks/useInfiniteScroll.js#L132) **NÃO foi tocado** (P3: proibido relaxar sem medição + nova aprovação). Registrar o veredito aqui após o teste manual; se travar em caso raro, veredito provável (a) "o operador rola e destrava".
  - **Item 4 (G2/G3 — expandir card acima da viewport desloca a leitura):** por P2 **aceita-se** o deslocamento (clique deliberado; sem `ResizeObserver`, sem âncora). Comportamento a documentar no teste manual; se ruim na prática, vira plano futuro.
- **Verificação:** a11y e tema validados por harness/estrutura + regra do CLAUDE.md. **Pendente de teste no browser:** G4 (item 3), deslocamento de scroll (item 4) e Tab+Enter ao vivo. Suíte pura 231/231 verde; **backend intocado** (`git diff` toca só os 4 arquivos de frontend do apêndice — nenhum `.py`).

---

## 5. Riscos e cuidados

| # | Ponto | Risco | Mitigação |
|---|-------|-------|-----------|
| **R1** | Prévia truncada | **Truncar a saída de `fmt()`** cortaria uma tag HTML no meio; com `dangerouslySetInnerHTML` isso gera markup quebrado e uma superfície de injeção | A prévia sai do `content` **cru**, é truncada como texto puro por `collapsedPreview` (F1) e renderizada por **interpolação normal do htm**, que escapa. Regra explícita na F3 item 3. |
| **R2** | Estado de expansão | `useState` **dentro** do card colaria o estado na mensagem errada após prepend (G1) | Card **controlado** (props `collapsed`/`onToggleCollapse`), estado no container chaveado por `cardStateKey`. Padrão idêntico ao [AuditLog.js:104,197](../web/static/js/components/AuditLog.js#L104). |
| **R3** | Default via efeito | `setState` em `useEffect` causa 2º render depois do bottom-scroll ([ContactDetail.js:208](../web/static/js/components/contacts/ContactDetail.js#L208)) → salto visível ao abrir a conversa (G5) | Default **derivado no render**: `!expandedCards.has(key)`. O único efeito é o reset por conversa (F4 item 3), que roda antes do paint. |
| **R4** | Colapsar encolhe a lista | O guard `scrollHeight - clientHeight <= 4` ([useInfiniteScroll.js:132](../web/static/js/hooks/useInfiniteScroll.js#L132)) pode travar "carregar anteriores" (G4) | Teste dedicado na F6 item 3. **Não** mexer no guard por impulso — tem histórico de regressão em cascata. |
| **R5** | `_id` ausente | `tool_call` via WS só carrega `_id` se o save deu certo ([messaging_service.py:531-532](../app/services/messaging_service.py#L531)) (G6) | `cardStateKey` com fallback `msg_id` → `role:ts` → índice, documentado. |
| **R6** | Modo escuro | Os hex `#2d1b4e`/`#2d1b0e` são inline e **não** entram nos overrides `html.dark` do [custom.css](../web/static/css/custom.css) | O chip **reusa a mesma string `style` da tabela** — comportamento idêntico ao card de hoje nos 2 temas. Elementos novos usam `currentColor`. Verificação na F6 item 2. |
| **R7** | `SYSTEM_CARD_VARIANTS` é dado morto | Executor edita a tabela, vê o teste verde e conclui que a feature funciona — sem nada mudar na tela (§3.2) | Chamado como falso-positivo em §3.2, no "Pronto quando" da F1, e a F3 item 3 **obriga** o card a ler `variant.style`/`variant.label` da tabela. |
| **R8** | Escopo vazando | Colapsar `private_note` por engano esconderia nota de operador — regressão séria de atendimento (D1) | Gate é `isCollapsibleRole` (2 roles), nunca `isSystemCardRole` (7 roles). Teste explícito na F1 item 5 cobrindo os 5 roles que **não** colapsam. |
| **R9** | Produção | A instância Empresa Exemplo roda `developer` | Feature 100% de UI (D5): sem migration, sem endpoint, sem payload novo. `git revert` do commit desfaz tudo. Nada a coordenar com deploy de banco. |
| **R10** | Plugins | Um plugin poderia depender do markup do card | Nenhum plugin grava `transcription`/`tool_call` (§3.2) e não há `Slot` dentro do `SystemMessageCard` — o único ponto de extensão próximo é `chat.header.banner` ([ContactDetail.js:403](../web/static/js/components/contacts/ContactDetail.js#L403)), intocado. |

---

## 6. Perguntas em aberto

**P1 — Transcrição de áudio e descrição de imagem seguem a mesma regra de prévia?**
Contexto: as duas gravam com o role `transcription` ([messaging_service.py:661](../app/services/messaging_service.py#L661) para áudio, [:1057](../app/services/messaging_service.py#L1057) para imagem/documento) e o `content` é cru, sem prefixo que as distinga (§2.2). Distingui-las exigiria olhar a mensagem `user` adjacente ou o `media_type` do vizinho — acoplamento novo.
(a) Mesma regra para as duas (truncar em ~70 chars). (b) Prefixar o rótulo por origem (`🔒 Transcrição do áudio` × `🔒 Descrição da imagem`).
✅ **DECIDIDO (2026-07-20): (a)** — o usuário pediu que "funcione nos dois". Como áudio e imagem **compartilham o role `transcription`**, os dois já ficam cobertos pelo mesmo caminho, sem trabalho extra e sem acoplamento novo. **Nenhuma fase muda** — o rótulo segue o genérico "Transcrição privada" que já existe, e a prévia do próprio texto ("A imagem mostra…" × "Fala aí, então…") desambigua na prática.
⚠️ Executor: se durante a F4 ficar evidente que o operador precisa distinguir a origem **antes** de abrir, isso é escopo NOVO (exige inspecionar o `media_type` da mensagem vizinha) — registre no Status de execução e pergunte, não implemente por conta.

**P2 — Expandir um card acima da viewport desloca a leitura. Ancorar?**
Contexto: não há `ResizeObserver` (G3) e o auto-scroll roda só em `[messages]` (G2). Expandir cresce o conteúdo e empurra o que está abaixo.
(a) Aceitar (o clique é deliberado; é como "ver mais" se comporta em qualquer app). (b) Capturar `getBoundingClientRect().top` do card antes do toggle e restaurar em `useLayoutEffect`.
✅ **DECIDIDO (2026-07-20): (a) aceitar** — (b) adicionaria um `useLayoutEffect` novo no caminho mais delicado do chat, com ganho pequeno. A F6 item 4 continua **documentando** o comportamento observado (não corrigindo); se ficar ruim na prática, vira plano futuro.

**P3 — Se a F6 item 3 provar que "carregar anteriores" trava (G4), o que fazer?**
Contexto: o guard de [useInfiniteScroll.js:132](../web/static/js/hooks/useInfiniteScroll.js#L132) existe para impedir auto-load em cascata — um bug real já corrigido ([ContactDetail.js:60-63](../web/static/js/components/contacts/ContactDetail.js#L60)).
(a) Nada (o operador rola e destrava). (b) Aumentar o tamanho da 1ª página de mensagens. (c) Relaxar o guard.
✅ **DECIDIDO (2026-07-20): decidir com a medição em mãos, na F6** — o executor **mede primeiro** (F6 item 3) e registra o veredito no Status de execução da F6. Se o caso for raro (conversa curta, quase toda de transcrição), fica (a). **Proibido** relaxar o guard sem a medição e sem nova aprovação — ele existe por causa de um bug de cascata já corrigido.

**P4 — O chip deve mostrar a hora?**
Contexto: o card expandido mostra (`float-right`, [:78-80](../web/static/js/components/contacts/SystemMessageCard.js#L78)). Numa linha só, a hora rouba ~40px da prévia.
(a) Sem hora no chip — a prévia é o que importa; a hora aparece ao expandir. (b) Com hora, prévia mais curta.
**Recomendação: (a)** — o mockup aprovado em D3 não a inclui, e o card fica ladeado por mensagens com hora própria. ✅ Alinhado com D3; executar como (a) salvo objeção na revisão visual da F4.

**P5 — O card inteiro é clicável ou só o chevron?**
**Recomendação: card inteiro** (alvo maior, menos precisão exigida) — já refletido na F3 item 3. Registrado aqui só para não ser reaberto na revisão. ✅ DECIDIDO (2026-07-20).

---

## 7. Apêndice — arquivos-chave

**Frontend — módulos puros (testáveis com `node --test`)**
- [web/static/js/services/messageView.js](../web/static/js/services/messageView.js) (115 linhas) — F1: `collapsible` na tabela, `isCollapsibleRole`, `collapsedPreview`, `cardStateKey`
- [web/static/js/services/messageView.test.js](../web/static/js/services/messageView.test.js) — F1: casos novos

**Frontend — componentes**
- [web/static/js/components/contacts/SystemMessageCard.js](../web/static/js/components/contacts/SystemMessageCard.js) (193 linhas) — F3: props controladas, `CollapsedCardChip`, header clicável, `data-mid` no `tool_call`
- [web/static/js/components/contacts/ContactDetail.js](../web/static/js/components/contacts/ContactDetail.js) (553 linhas) — F2 (key), F4 (estado + reset + fork), F5 (deep-link)

**Frontend — leitura obrigatória, sem edição prevista**
- [web/static/js/hooks/useInfiniteScroll.js](../web/static/js/hooks/useInfiniteScroll.js) — `useReverseInfiniteScroll` ([:124-151](../web/static/js/hooks/useInfiniteScroll.js#L124)); o guard de G4/R4
- [web/static/js/components/AuditLog.js](../web/static/js/components/AuditLog.js) — o padrão "container guarda o id expandido, filho é controlado" ([:104](../web/static/js/components/AuditLog.js#L104), [:197](../web/static/js/components/AuditLog.js#L197), [:458-459](../web/static/js/components/AuditLog.js#L458))
- [web/static/js/components/Executions.js](../web/static/js/components/Executions.js) — `Collapsible` + `expandSignal` ([:259-272](../web/static/js/components/Executions.js#L259)), caso D4 seja reaberto
- [web/static/css/custom.css](../web/static/css/custom.css) — overrides `html.dark` (contexto de R6)

**Backend — leitura de referência apenas (nenhuma mudança nesta feature)**
- [app/services/messaging_service.py:474-533](../app/services/messaging_service.py#L474) — `broadcast_tool_calls`: formato do `content` e o `_id` condicional (R5)
- [app/services/messaging_service.py:625-668](../app/services/messaging_service.py#L625) / [:1040-1057](../app/services/messaging_service.py#L1040) — gravação de `transcription`
- [db/repositories/message_repo.py:531-578](../db/repositories/message_repo.py#L531) — `_row_to_dict`: os campos que o frontend recebe
- [db/repositories/_mapping.py:103-106](../db/repositories/_mapping.py#L103) — `LIST_PANEL_ONLY_ROLES`, a fonte da verdade dos 7 roles

---

## 8. Checklist de verificação

Aplicável ao final de cada fase (e integralmente antes do commit final):

- [ ] `node --test web/static/js/services/messageView.test.js` **verde** (casos antigos + novos)
- [ ] `node --test` nos demais módulos puros do chat não regrediu: `messages.test.js`, `systemCta.test.js`, `conversationRows.test.js`
- [ ] Suíte Postgres verde — `WHATSBOT_TEST_DB_URL` setada, `venv/bin/python -m pytest tests/test_endpoints.py -q` (esperado: **inalterada**; a feature não toca o backend — se algo mudar, é sinal de que o escopo vazou)
- [ ] **Reload da página** com a conversa aberta: cards voltam colapsados, sem flash de conteúdo expandido
- [ ] **Back/forward** do navegador entre conversas: sem estado vazado entre elas (D2)
- [ ] **Modo escuro** ligado: os dois chips legíveis, contraste equivalente ao card expandido (regra obrigatória do CLAUDE.md)
- [ ] **Modo claro**: idem
- [ ] `private_note`, `system`, `system_notice`, `error`, `conversation_event` renderizam **exatamente como antes** (D1/R8) — comparar com print anterior
- [ ] "Carregar anteriores" dispara e ancora corretamente, com cards colapsados e expandidos (G1/G4)
- [ ] Mensagem nova por WS (transcrição e tool_call) entra colapsada e o chat rola ao fim
- [ ] Deep-link/busca para uma transcrição abre o card (F5)
- [ ] Teclado: Tab alcança o chip, Enter/Espaço alternam, `aria-expanded` reflete o estado
- [ ] Sem migration, sem `ConfigKey` nova, sem chave de `localStorage` nova (D2/D5) — `git diff --stat` deve tocar **apenas** os 4 arquivos do apêndice
