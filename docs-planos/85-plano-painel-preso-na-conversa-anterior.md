# Plano 85 — Painel preso na conversa anterior: carregamento do detalhe sem guarda, sem estado e sem erro

> **Status:** ✅ IMPLEMENTADO (2026-07-28) — 7 de 8 fases; F0 é operacional e segue pendente · **Escopo:** médio
> **Origem:** Bug reportado em produção (`atendimento.coolify.redesbrasil.com.br`) — a operadora clica em outra conversa e o painel continua exibindo a conversa ANTERIOR (nome do contato antigo + todas as bolhas antigas), enquanto o telefone do cabeçalho e a URL `/conversations/<id>` já são da conversa NOVA. **Método:** leitura do código real (`arquivo:linha`) + inspeção do banco de produção `whatsbot` via MCP vault (conversas, contatos, mensagens, usuários, cargos, canais, sessões, índices).
> O detalhe da conversa é carregado por um efeito que **não tem guarda de resposta obsoleta, não tem `.catch()`, não reexibe o estado de carregamento depois da primeira conversa e não re-tenta quando a mesma linha é clicada de novo**. Qualquer falha ou lentidão de UMA requisição deixa o painel exibindo a conversa errada de forma **silenciosa e permanente**. O plano corrige a estrutura (o que torna o sintoma possível) e ataca o gatilho suspeito que o tornou frequente após o deploy de 25/07.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | ✅ (2026-07-28) O bug é **de frontend**. Verificado no banco de produção: a conversa `15013` contém **apenas** mensagens do Renê César; a thread que aparecia na tela era a `14866` (contato `~Natan`, `5511993124662`) — outra conversa, real e aberta | Nenhuma mudança de backend de dados, nenhuma migration. O plano é quase todo `web/static/js/` |
| D2 | ✅ (2026-07-28) Corrigir a **estrutura**, não só o gatilho | Mesmo que o gatilho de hoje seja eliminado, qualquer 500/502/queda de rede volta a produzir o mesmo sintoma. As Fases A1–A3 valem por si |
| D3 | ✅ (2026-07-28) O sintoma é **perigoso**, não só feio | A operadora lê o histórico do cliente A e escreve a resposta que será entregue ao cliente B (o compositor usa as props novas, corretas). Prioridade alta |
| D4 | ✅ (2026-07-28) Já foram **descartados com dados de produção**: RBAC/escopo de caixa, expiração de sessão, índices trigram ausentes, volume de mensagens | Ver "Falsos positivos descartados" (§3). Não reabrir sem evidência nova |

---

## 1. Resumo executivo

O painel do chat lê **duas fontes de verdade com tempos diferentes**: `phone`/`conversationId`/`channelId` vêm do estado `selected*`, atualizado **de forma síncrona no clique**; `info`/`contact`/`messages` vêm de `contactData`, substituído **só quando o fetch responde com sucesso** ([Contacts.js:427-435](../web/static/js/components/contacts/Contacts.js#L427)). O efeito que faz esse fetch ([useConversationSelection.js:172-236](../web/static/js/components/contacts/hooks/useConversationSelection.js#L172)) não tem token de sequência, não tem `.catch()`, só liga o "Carregando..." na primeira conversa desde que a tela montou, e não re-dispara quando a mesma linha é clicada outra vez. Resultado: **latência alta, resposta fora de ordem ou falha de transporte ⇒ cabeçalho da conversa nova sobre a thread da conversa velha, sem spinner, sem erro, sem saída** exceto abrir outra conversa que carregue bem ou recarregar a página.

A correção tem três frentes: **(A)** endurecer o carregamento do detalhe (guarda de obsolescência + estado de carregamento por conversa + erro visível com "Tentar de novo"); **(B)** eliminar a re-tentativa por render do catálogo de providers, que hoje pode disparar uma requisição a cada re-render enquanto o fetch não tiver sucesso; **(C)** extrair a lógica de aplicação da resposta (hoje **triplicada**) para um módulo puro coberto por `node --test`.

---

## 2. Como funciona hoje (mapa)

### 2.1 O caminho do clique até a tela

| Passo | Local | Comportamento |
|-------|-------|---------------|
| Clique na linha | [useConversationSelection.js:83-117](../web/static/js/components/contacts/hooks/useConversationSelection.js#L83) `selectContact` | `setSelected(row.phone)` + `setSelectedConvId(...)` + `setSelectedChannelId(...)` + `history.pushState('/conversations/<id>')` (linha 111). **Síncrono, nunca falha** |
| Efeito de carga | [useConversationSelection.js:172](../web/static/js/components/contacts/hooks/useConversationSelection.js#L172), deps `[selected, selectedConvId]` (linha 236) | Dispara o fetch do detalhe |
| Spinner | [:180](../web/static/js/components/contacts/hooks/useConversationSelection.js#L180) `if (!hasLoadedDetail.current) setLoadingDetail(true);` | ⚠️ `hasLoadedDetail` vira `true` na linha 233 e **nunca volta a `false`** enquanto a tela do hub estiver montada ⇒ o "Carregando..." só aparece na **primeira** conversa |
| Requisição | [:203-206](../web/static/js/components/contacts/hooks/useConversationSelection.js#L203) | `getConversationMessages(convId, isPageVisible)` ([api.js:234](../web/static/js/services/api.js#L234)) ou `getContact(...)` para linha sem atendimento |
| Aplicação | [:207-235](../web/static/js/components/contacts/hooks/useConversationSelection.js#L207) | `loader.then(res => { if (res.ok) { … setContactData(data) } … })`. ⚠️ **sem `.catch()`**, ⚠️ **sem token de sequência**, ⚠️ `res.ok === false` não faz **nada** |
| Render | [Contacts.js:425-435](../web/static/js/components/contacts/Contacts.js#L425) | `loadingDetail ? "Carregando..." : <ContactDetail phone=${selected} … info=${info} contact=${contactData} messages=${messages}>`; `messages`/`info` derivam de `contactData` ([:307-308](../web/static/js/components/contacts/Contacts.js#L307)) |
| Cabeçalho | [ContactDetail.js:90](../web/static/js/components/contacts/ContactDetail.js#L90) e [:99](../web/static/js/components/contacts/ContactDetail.js#L99) | `headerSubtitle = useContactSubtitle(phone, …)` (prop **nova**) × `displayName` derivado de `info.name` (**antigo**) — é literalmente onde as duas fontes divergem na tela |

**Evidência de produção que fecha o diagnóstico:**

| Conversa | Contato no banco | Telefone no banco | O que o cabeçalho mostrava |
|---|---|---|---|
| `15020` | `~Rafael` | `559186315238` | nome **"Natan"** + telefone `559186315238` |
| `15013` | `~Renê César` | `554599988436` | nome **"Natan"** + telefone `554599988436` |
| `14866` | `~Natan` | `5511993124662` | — (era a thread realmente renderizada) |

O telefone acompanhou a conversa clicada em ambos os casos; o nome e as bolhas ficaram na `14866`. Isso prova que `selected` atualizou e o componente re-renderizou — logo o efeito da linha 172 **rodou** — e que só `contactData` ficou para trás.

### 2.2 Os quatro modos de falha (todos no mesmo efeito)

| # | Situação | Consequência hoje |
|---|----------|-------------------|
| 1 | Requisição **lenta** (ainda em voo) | Painel mostra a conversa anterior inteira, **sem nenhuma indicação visual**, porque o spinner está desligado desde a 2ª conversa ([:180](../web/static/js/components/contacts/hooks/useConversationSelection.js#L180)) |
| 2 | Respostas **fora de ordem** (clica A, clica B, A responde depois) | `setContactData(A)` sobrescreve B. Painel **permanentemente** errado |
| 3 | `res.ok === false` (404/500/403) | O `if (res.ok)` na linha 208 não faz nada: `contactData` continua na conversa anterior, **sem erro na tela** (só 403 gera toast, via [httpClient.js:105](../web/static/js/services/httpClient.js#L105)) |
| 4 | Promise **rejeita** (rede caiu, 502 em redeploy) | `fetch` sem `try/catch` em [httpClient.js:100](../web/static/js/services/httpClient.js#L100) + `.then` sem `.catch` ⇒ *unhandled rejection*; `setLoadingDetail(false)` (linha 234) **nunca roda** — se for a 1ª conversa, a tela fica em **"Carregando..." eterno** |

Em todos os casos, **reclicar a mesma linha não re-tenta**: as deps `[selected, selectedConvId]` (linha 236) não mudaram.

### 2.3 As curas existentes (e por que não bastam)

| Mecanismo | Local | Por que não cobre |
|---|---|---|
| `reloadOpenThread` | [:248-274](../web/static/js/components/contacts/hooks/useConversationSelection.js#L248) | Só dispara na **reconexão do WebSocket** ([useConversationWsEvents.js:145-148](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L145)). Se o WS não caiu, nada acontece |
| `scheduleOpenThreadResync` | [useConversationWsEvents.js:124-132](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L124) | Só dispara quando chega **mensagem nova** na conversa aberta |
| Padrão correto já existente no repo | [useConversationList.js:54-56](../web/static/js/components/contacts/hooks/useConversationList.js#L54) (`fetchSeqRef`/`fetchAbortRef`), [:140-144](../web/static/js/components/contacts/hooks/useConversationList.js#L140), [:161](../web/static/js/components/contacts/hooks/useConversationList.js#L161) | A **lista** ganhou token de sequência + abort no plano 62 F3; o **detalhe** nunca ganhou. É o mesmo problema, resolvido só de um lado |

### 2.4 O gatilho suspeito (novo desde 25/07)

[providerCatalog.js:53-69](../web/static/js/services/providerCatalog.js#L53) — módulo introduzido pelo merge `ea550b2` (plano 76 · H1, 25/07):

```js
function ensureLoaded() {
  if (_descriptors !== null || _loading) return;   // :54
  _loading = true;
  (async () => {
    try {
      const res = await listChannelProviders();
      if (res && res.ok && res.data) { _descriptors = map; bump(); }   // :59-64  só grava no sucesso
    } catch (_) { /* mantém fallback; retry na próxima leitura */ }    // :66
    finally { _loading = false; }                                       // :67
  })();
}
```

Enquanto a requisição **não** retornar `ok:true`, `_descriptors` permanece `null` e `_loading` volta a `false` — o próprio comentário assume o retry. E `ensureLoaded()` é chamado de dentro de `descriptorFor()` ([:78](../web/static/js/services/providerCatalog.js#L78)), que é chamado por `providerLabel`/`providerTint`, que são chamados **no corpo de render** do `ChannelChip` ([ChannelChip.js:26](../web/static/js/components/contacts/ChannelChip.js#L26)).

⚠️ **O chip é renderizado em TODA linha da sidebar, sem gate**: [ContactList.js:692](../web/static/js/components/contacts/ContactList.js#L692) — o comentário na linha 688 diz explicitamente que ele "é sempre visível — não passa pelo gate `showChannel`, que segue valendo só no cabeçalho do chat" ([ContactDetail.js:479](../web/static/js/components/contacts/ContactDetail.js#L479)). Produção tem **7 canais / 4 providers** (`gowa`, `telegram`, `website` ×3, `whatsapp_cloud` ×2), então os chips existem e renderizam.

O endpoint é gated por **`channel.manage`** ([channels.py:272](../server/routes/channels.py#L272)) — uma permissão de **gestão** para um dado que o **hub inteiro** usa só para pintar rótulo e cor. Nesta instalação o cargo "Atendente" **tem** `channel.manage` (verificado no banco), então o 403 não é o gatilho aqui; mas o acoplamento é frágil por construção e qualquer instalação sem esse grant entra em re-tentativa perpétua.

---

## 3. Inventário / análise

| # | Item | Local | O que falta | Abordagem | Risco | Esforço |
|---|------|-------|-------------|-----------|-------|---------|
| 1 | Guarda de resposta obsoleta no detalhe | [useConversationSelection.js:207](../web/static/js/components/contacts/hooks/useConversationSelection.js#L207) | Nenhuma | Token de sequência (`detailSeqRef`), espelhando [useConversationList.js:140,161](../web/static/js/components/contacts/hooks/useConversationList.js#L140) | Baixo | S |
| 2 | Estado de carregamento por conversa | [:180](../web/static/js/components/contacts/hooks/useConversationSelection.js#L180) + [:233](../web/static/js/components/contacts/hooks/useConversationSelection.js#L233) | `hasLoadedDetail` é one-shot da montagem | Trocar por "carregou **esta** conversa": comparar a chave da thread, não um booleano global | Médio (UX: flash) | S |
| 3 | Tratamento de falha + erro visível | [:207-235](../web/static/js/components/contacts/hooks/useConversationSelection.js#L207) | Sem `.catch`; `res.ok===false` ignorado | `.catch()` + `detailError` + painel de erro com "Tentar de novo" | Baixo | M |
| 4 | Re-tentar ao reclicar a mesma linha | [:236](../web/static/js/components/contacts/hooks/useConversationSelection.js#L236) | Deps inalteradas ⇒ no-op | `retryNonce` no estado, incluída nas deps do efeito | Baixo | S |
| 5 | Nunca exibir thread de A sob cabeçalho de B | [Contacts.js:427-435](../web/static/js/components/contacts/Contacts.js#L427) | `contactData` sobrevive à troca | Garantia estrutural: `contactData` carrega a chave da conversa a que pertence e o render só o usa se casar | Médio | M |
| 6 | `ensureLoaded` re-tenta por render | [providerCatalog.js:53-69](../web/static/js/services/providerCatalog.js#L53) | Sem limite de tentativas, sem backoff | Contador de tentativas + backoff + `refresh()` explícito; **nunca** disparar rede a partir do corpo de render | Baixo | M |
| 7 | Catálogo de apresentação atrás de `channel.manage` | [channels.py:264-273](../server/routes/channels.py#L264) | Permissão de gestão para dado de UI | Regatear em `conversation.reply` (precedente nas linhas [51](../server/routes/channels.py#L51), [67](../server/routes/channels.py#L67), [80](../server/routes/channels.py#L80)) — o descriptor não traz valor de credencial, só definição de campo | Baixo | S |
| 8 | Lógica de aplicação da resposta **triplicada** | [:218-231](../web/static/js/components/contacts/hooks/useConversationSelection.js#L218), [:264-272](../web/static/js/components/contacts/hooks/useConversationSelection.js#L264), [:300-310](../web/static/js/components/contacts/hooks/useConversationSelection.js#L300) | Merge de buffer + hidratação de `failed` copiada 3× | Extrair para módulo puro em `services/` + `node --test` | Baixo | M |
| 9 | Teste do catálogo não cobre re-tentativa | [providerCatalog.test.js](../web/static/js/services/providerCatalog.test.js) | Roda **com** o fetch falhando e só valida o fallback | Adicionar caso que conta chamadas de fetch sob falha | Baixo | S |

### Falsos positivos descartados

| Suspeita | Por que NÃO é o problema |
|----------|--------------------------|
| Dados corrompidos / conversas misturadas no banco | A conversa `15013` só tem mensagens do Renê César (`Ótimo dia Renê`, `Maravilha Renê`, aviso `~Renê César`). O backend devolve certo — **D1** |
| RBAC / escopo de caixa (404 por inbox) | Ambas as conversas são da inbox `21` e a operadora (user `4`) é membro da `21` (`inbox_members`). `_inbox_hidden` ([conversations.py:109](../server/routes/conversations.py#L109)) não dispara |
| Sessão expirada / 401 | Sessão criada em 20/07, válida até 19/08 (`user_sessions`). Um 401 além disso derrubaria para o login via [AuthGate.js:86](../web/static/js/components/shell/AuthGate.js#L86) |
| Busca lenta do plano 62 saturando o banco | Os índices trigram **estão** em produção: `idx_contacts_name_trgm` e `idx_msg_content_trgm` |
| Conversa "pesada" demais | As conversas abertas dela têm de 14 a 223 mensagens; a página é de 50 ([pagination.py:21](../server/pagination.py#L21)) |
| `outbound`/`media_limits` deixando a rota lenta | `capabilities()`/`supports()` são lookup em memória e `session_open()` é aritmética pura ([outbound.py:40-72](../channels/outbound.py#L40)) — sem rede no caminho de leitura |
| Commit de bulk selection (`6556929`) | Só troca o item de menu "Selecionar todas"/"Desmarcar todas" e adiciona `deselectAll`; não toca seleção de conversa |
| Planos 81/82 (`afdb503`, `72b549d`) | 82 mexe só em `server/routes/channel_webhook.py`; 81 gateia rotas de **plugin de canal**, não a leitura de atendimento |
| Toast de 403 fechando um laço de render | [notify.js](../web/static/js/services/notify.js) é um barramento próprio, com dedupe de 4s, e só o `<Toaster/>` renderiza — não re-renderiza a sidebar |

---

## 4. Fases / Roadmap

O núcleo (A) e o gatilho (B) são **arquivos diferentes** e podem andar em paralelo. A confirmação em produção (F0) não bloqueia nada, mas decide se B é causa ou só higiene. Dentro de A, as fases são sequenciais **de propósito**: todas mexem no mesmo efeito ([:172-236](../web/static/js/components/contacts/hooks/useConversationSelection.js#L172)) e a disciplina do repo é **um refactor por commit**.

```
WAVE 0   F0 (confirmação em prod, sem código) · A1 (guarda de sequência) · B1 (catálogo)
            │                                      │                          │
            │                                      │ (mesmo efeito)           │
WAVE 1      │                                   A2 (estado de carga)       B2 (regatear endpoint)
            │                                      │
WAVE 2      │                                   A3 (erro + retry)  →  A4 (garantia estrutural)
            │                                      │
WAVE 3   C1 (extrair módulo puro + node --test)  ←──┘   [depende de: A1..A4]
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|------|------|-----------|-------|-------|----------------|
| 0 | F0 | Confirmar o gatilho em produção (operacional) | 🟢 | Nenhum | Chip de canal colorido **ou** contagem de requisições do catálogo conhecida |
| 0 | A1 | Token de sequência no carregamento do detalhe | 🔴 `[bloqueia: A2, A3, A4]` | Baixo | Resposta atrasada de uma conversa não sobrescreve outra |
| 0 | B1 | `ensureLoaded` para de re-tentar por render | 🟢 | Baixo | `node --test providerCatalog.test.js` verde com o novo caso |
| 1 | A2 | Estado de carregamento por conversa | 🔴 `[depende de: A1]` | Médio | Toda troca de conversa mostra carregamento |
| 1 | B2 | Regatear `/api/channels/providers` | 🟢 `[depende de: B1]` | Baixo | `pytest tests/` verde; operador sem `channel.manage` vê os chips coloridos |
| 2 | A3 | Erro visível + "Tentar de novo" + reclique re-tenta | 🔴 `[depende de: A2]` | Baixo | Falha simulada mostra erro e o botão recarrega |
| 2 | A4 | Garantia estrutural (thread nunca sob cabeçalho errado) | 🔴 `[depende de: A3]` | Médio | Impossível renderizar `contactData` de outra conversa |
| 3 | C1 | Extrair aplicação da resposta (triplicada) + testes | 🔴 `[depende de: A4]` | Baixo | `node --test` verde no módulo novo |

---

### Fase F0 — Confirmar o gatilho em produção (sem código)

**Objetivo:** decidir se o catálogo de providers é o gatilho que tornou o bug frequente após 25/07, ou se é apenas dívida a pagar.

**Itens:**
- `[paralelo]` **Sinal visual, sem ferramenta**: na sidebar da operadora, o chip de canal de cada linha ([ContactList.js:692](../web/static/js/components/contacts/ContactList.js#L692)) deve estar **colorido** conforme o provider. Se estiver **cinza** para `telegram`/`website`/`whatsapp_cloud`, o catálogo está no fallback — e o fallback só conhece `gowa` e `test` ([providerCatalog.js:28-31](../web/static/js/services/providerCatalog.js#L28)) — ou seja, o fetch **falhou**.
- `[paralelo]` **Contagem retroativa** (o navegador guarda o histórico mesmo sem DevTools aberto antes), no Console do painel dela:
  ```js
  performance.getEntriesByType('resource').filter(r => r.name.includes('channels/providers')).length
  ```
  `1`–`2` ⇒ catálogo carregou, B vira só higiene. Dezenas/centenas (ou estourando o buffer de 250) ⇒ é o gatilho, e B1 sobe para prioridade máxima.
- `[paralelo]` Se possível, capturar o Network **com "Preserve log" ligado e a caixa de filtro de texto vazia**, filtrando por `Fetch/XHR`, e verificar o status/tempo de `atendimentos/<id>/messages` durante um travamento.

**Pronto quando:** registrado neste plano qual dos dois cenários é o real. **Nenhuma linha de código depende deste resultado** — A1–A4 valem nos dois.

#### Status de execução — Fase F0
**Estado:** ⏸️ Pendente (operacional — depende do navegador da operadora)
- **O que foi feito:** nada de código. A fase não bloqueia nenhuma outra e as A1–A4 valem nos dois cenários, então a implementação seguiu sem esperar por ela.
- **Como foi feito / decisões:** as duas verificações continuam válidas exatamente como escritas (cor dos chips na sidebar; contagem de `channels/providers` em `performance.getEntriesByType('resource')`). A B2 REMOVE a causa mais provável de o fetch falhar nesta instalação (o 403 por `channel.manage`), então a contagem só é conclusiva se medida ANTES do deploy destas correções.
- **Problemas / pendências:** medir em produção antes do deploy, se ainda houver janela; senão a P4 fica encerrada como "não determinado" — B1/B2 valem por si.
- **Verificação:** _(pendente)_

---

### Fase A1 — Token de sequência no carregamento do detalhe

**Objetivo:** uma resposta que chega depois de a seleção ter mudado é **descartada**, nunca aplicada.

**Itens:**
- `[sequencial]` Criar `detailSeqRef = useRef(0)` junto dos demais refs ([useConversationSelection.js:56-65](../web/static/js/components/contacts/hooks/useConversationSelection.js#L56)), espelhando `fetchSeqRef` ([useConversationList.js:54](../web/static/js/components/contacts/hooks/useConversationList.js#L54)).
- `[sequencial]` No efeito ([:172](../web/static/js/components/contacts/hooks/useConversationSelection.js#L172)), antes de montar o `loader` (linha 203): `const token = ++detailSeqRef.current;`.
- `[sequencial]` Na primeira linha do `.then` ([:207](../web/static/js/components/contacts/hooks/useConversationSelection.js#L207)): `if (token !== detailSeqRef.current) return;` — mesmo formato da linha 161 da lista.
- `[sequencial]` Aplicar a MESMA guarda em `reloadOpenThread` ([:260](../web/static/js/components/contacts/hooks/useConversationSelection.js#L260)) e `loadOlder` ([:296](../web/static/js/components/contacts/hooks/useConversationSelection.js#L296)) — os dois também chamam `setContactData` sem verificar se a thread ainda é a mesma.
  - ⚠️ `loadOlder` já tem `loadingOlderRef` contra concorrência **de si mesmo**, mas não contra **troca de conversa** no meio do voo: hoje a página anterior de A pode ser prependada na thread de B.
- ⚠️ **NÃO abortar** o request anterior nesta fase — ver P1 (abortar cancelaria o `mark_read` da conversa que ela acabou de abrir).

**Pronto quando:** com throttling "Slow 3G", clicar rápido em A → B → C deixa o painel em **C** (nunca volta para A ou B) e a URL bate com o conteúdo.

#### Status de execução — Fase A1
**Estado:** ✅ Concluída — commit `7a31212`
- **O que foi feito:** `detailSeqRef` no hook de seleção, aplicado aos TRÊS pontos que gravam `contactData`.
- **Como foi feito / decisões:** o efeito de seleção e `reloadOpenThread` INCREMENTAM o token (só a carga mais recente grava); `loadOlder` apenas CAPTURA — paginar para trás não pode invalidar uma carga de seleção em voo, mas trocar de conversa invalida a página (era o buraco real: `loadingOlderRef` só protegia `loadOlder` contra si mesmo, então a página anterior de A podia ser prependada na thread de B). Sem `AbortController`, conforme P1.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --input-type=module --check` no arquivo; comportamento coberto adiante pelos testes puros da C1 (`prependOlder`/`applyThreadResponse`).

---

### Fase A2 — Estado de carregamento por conversa

**Objetivo:** o operador nunca mais olha para a thread da conversa anterior achando que é a que ele abriu.

**Itens:**
- `[sequencial]` `[depende de: A1]` Substituir o booleano one-shot `hasLoadedDetail` ([:56](../web/static/js/components/contacts/hooks/useConversationSelection.js#L56), [:180](../web/static/js/components/contacts/hooks/useConversationSelection.js#L180), [:233](../web/static/js/components/contacts/hooks/useConversationSelection.js#L233)) por "qual thread já está carregada" — guardar a chave (`conv:<id>` ou `phone:<n>`, o mesmo `bufKey` já montado na linha 183) e ligar o carregamento sempre que a chave pedida ≠ chave carregada.
- `[sequencial]` Garantir que `setLoadingDetail(false)` rode em **todos** os desfechos (sucesso, `ok:false` e rejeição) — hoje está só dentro do `.then` (linha 234), então uma rejeição na primeira conversa trava a tela em "Carregando..." para sempre.
- `[sequencial]` Ver P2 sobre o *flash*: em rede rápida o spinner pisca. Opções na pergunta em aberto.

**Pronto quando:** trocar de conversa com rede lenta mostra o estado de carregamento **toda vez**; nenhuma troca exibe conteúdo da conversa anterior; recarregar com a rede caída mostra erro (A3), não "Carregando..." infinito.

#### Status de execução — Fase A2
**Estado:** ✅ Concluída — commit `44d7c52`
- **O que foi feito:** `hasLoadedDetail` (booleano one-shot da montagem) substituído por `loadedThreadKeyRef` — QUAL thread está carregada, na chave canônica `threadKeyOf`.
- **Como foi feito / decisões:** a chave prefere o atendimento ao telefone (`conv:<id>` > `phone:<n>`), igual ao `rowKeyFor` da sidebar. Isso foi deliberado: no deep-link `/conversations/:id` a tela abre com `selected` nulo e adota o telefone da resposta depois — com uma chave telefone-primeiro ela mudaria no meio e o painel piscaria "Carregando..." num refetch da MESMA conversa. Fechados também os dois desfechos que prendiam o estado: `.catch()` no loader (sem ele uma rejeição nunca rodava `setLoadingDetail(false)` e a tela travava em "Carregando..." para sempre) e a limpeza da chave ao deselecionar.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --input-type=module --check`; suíte de endpoints intacta (1626 passed).

---

### Fase A3 — Erro visível, "Tentar de novo" e reclique que re-tenta

**Objetivo:** falha de carregamento vira uma mensagem acionável, não um painel mudo com a conversa errada.

**Itens:**
- `[sequencial]` `[depende de: A2]` Adicionar `.catch()` ao `loader` ([:207](../web/static/js/components/contacts/hooks/useConversationSelection.js#L207)) — engolir `AbortError` se/quando houver abort (padrão da [useConversationList.js:202](../web/static/js/components/contacts/hooks/useConversationList.js#L202)) e tratar o resto como erro real.
- `[sequencial]` Tratar `res.ok === false` explicitamente (hoje o `if (res.ok)` da linha 208 simplesmente não faz nada): guardar `detailError` com a mensagem normalizada que [handleErrorResponse](../web/static/js/services/httpClient.js#L69) já devolve.
- `[sequencial]` Expor `detailError` no retorno do hook ([:314-326](../web/static/js/components/contacts/hooks/useConversationSelection.js#L314)) e renderizar em [Contacts.js:425](../web/static/js/components/contacts/Contacts.js#L425) um estado de erro com botão **"Tentar de novo"** — nas cores semânticas `wa-*` (ver regra de modo escuro no `CLAUDE.md`).
- `[sequencial]` `retryNonce` no estado, incluída nas deps do efeito ([:236](../web/static/js/components/contacts/hooks/useConversationSelection.js#L236)), incrementada pelo botão **e** por `selectContact` quando a linha clicada já é a selecionada ([:83](../web/static/js/components/contacts/hooks/useConversationSelection.js#L83)) — assim reclicar a mesma conversa re-tenta.

**Pronto quando:** com o backend derrubado, abrir uma conversa mostra erro + botão; religar o backend e clicar em "Tentar de novo" carrega a thread certa sem F5.

#### Status de execução — Fase A3
**Estado:** ✅ Concluída — commit `6df3340`
- **O que foi feito:** `detailError` + painel de erro com "Tentar de novo" + `retryNonce` nas deps do efeito.
- **Como foi feito / decisões:** a mensagem de `res.ok === false` vem já normalizada de `handleErrorResponse` (cobre as duas formas de corpo do backend); a rejeição de transporte vira uma mensagem de conexão e `AbortError` é engolido. O nonce é incrementado pelo botão E por um clique na linha JÁ selecionada — assim a própria sidebar recupera um painel travado, sem F5. Painel em classes semânticas `wa-*` (`bg-wa-panel`/`text-wa-text`/`text-wa-secondary`/`bg-wa-teal`), legível nos dois temas.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --input-type=module --check` nos dois arquivos.

---

### Fase A4 — Garantia estrutural: thread nunca sob o cabeçalho errado

**Objetivo:** tornar o sintoma **impossível por construção**, e não apenas improvável — mesmo que um bug futuro reintroduza uma corrida.

**Itens:**
- `[sequencial]` `[depende de: A3]` Carimbar em `contactData` a chave da conversa a que ele pertence (o mesmo `bufKey`) no momento em que é gravado ([:231](../web/static/js/components/contacts/hooks/useConversationSelection.js#L231)).
- `[sequencial]` Em [Contacts.js:307-308](../web/static/js/components/contacts/Contacts.js#L307), derivar `messages`/`info` **apenas** quando o carimbo casar com a seleção corrente; caso contrário tratar como "carregando" (A2). Alternativa equivalente e mais barata: passar `key=${selectedConvId ?? selected}` no `<ContactDetail>` ([:427](../web/static/js/components/contacts/Contacts.js#L427)), forçando a remontagem e descartando estado interno na troca.
  - ⚠️ A opção `key=` remonta o componente: verificar o efeito sobre `scrollToMsg`, `useComposer` (rascunho é persistido em `localStorage`, então sobrevive) e `useReverseInfiniteScroll`. Ver P3.

**Pronto quando:** com uma resposta forçada de outra conversa (mock no DevTools), o painel **não** a exibe — mostra carregamento/erro.

#### Status de execução — Fase A4
**Estado:** ✅ Concluída — commit `3e60769`
- **O que foi feito:** carimbo `_threadKey` em `contactData` + guarda no RENDER do container + a mesma guarda no append de WS.
- **Como foi feito / decisões:** escolhida a opção **(b)** da P3 (carimbo + comparação), não `key=` — sem remontagem, então scroll, `scrollToMsg` e o estado interno do compositor ficam intactos. Descoberta que justifica a fase: as guardas A1–A3 vivem DENTRO do efeito, que roda depois do paint, então entre o clique e a primeira execução do efeito sempre sobrava um frame com o cabeçalho novo sobre as bolhas antigas — só uma guarda no render fecha isso. A comparação também protege o drawer de contato, que renderiza FORA do ramo de carregamento e podia exibir o contato anterior.
- **Problemas / pendências:** extensão além do escrito, deliberada: o append de WS tratava `prev != null` como "detalhe carregado" e anexava ao objeto da conversa ANTERIOR — a mensagem se perdia quando o loader o substituía. Agora um `prev` com carimbo de outra thread cai no mesmo caminho de buffer que o loader drena.
- **Verificação:** `node --input-type=module --check` nos três arquivos; auditados todos os `setContactData` do hub — são atualizações funcionais com spread (preservam o carimbo) ou `setContactData(null)` acompanhado de deselect.

---

### Fase B1 — `ensureLoaded` para de re-tentar a cada render

**Objetivo:** o catálogo de providers nunca mais dispara rede a partir do corpo de render, e uma falha não vira uma requisição por re-render.

**Itens:**
- `[paralelo]` Em [providerCatalog.js:53-69](../web/static/js/services/providerCatalog.js#L53): contador de tentativas + backoff (ex.: 3 tentativas, com espera crescente) e um estado explícito de "desisti" — em vez do `_loading = false` na linha 67 rearmar tudo na próxima leitura.
- `[paralelo]` Expor `refresh()` para o retry deliberado (ex.: ao (re)estabelecer sessão em [AuthGate.js](../web/static/js/components/shell/AuthGate.js), ou quando a tela Canais salvar um canal), em vez de depender do render.
- `[paralelo]` Manter o contrato D3 do plano 76 intacto: falha ⇒ fallback estático ([:28-31](../web/static/js/services/providerCatalog.js#L28)) e provider desconhecido ⇒ próprio id em cinza. **Nenhuma tela pode quebrar** por falta de catálogo.
- `[paralelo]` Teste em [providerCatalog.test.js](../web/static/js/services/providerCatalog.test.js): com `listChannelProviders` sempre falhando, N leituras de `providerLabel` devem produzir **no máximo K** chamadas de fetch (hoje o arquivo roda justamente com o fetch falhando e só valida o fallback — a re-tentativa nunca é exercida).

**Pronto quando:** `node --test web/static/js/services/providerCatalog.test.js` verde, incluindo o caso novo de contagem de chamadas.

#### Status de execução — Fase B1
**Estado:** ✅ Concluída — commit `89212a9`
- **O que foi feito:** teto de `MAX_ATTEMPTS` (3) com backoff exponencial agendado por timer, `refresh()` para a re-tentativa deliberada, e um arquivo de teste novo.
- **Como foi feito / decisões:** a re-tentativa saiu do caminho de render — nenhuma leitura de getter dispara rede por conta própria. `refresh()` é chamado quando a sessão é estabelecida (`AuthGate`, `authState === 'ready'`): as tentativas gastas antes do login voltam 401 e deixariam os selos cinza até um F5. `refresh()` NÃO limpa os descriptors atuais, então nenhuma tela pisca para o fallback. Contrato D3 do plano 76 preservado.
- **Problemas / pendências:** o teste ficou em arquivo SEPARADO (`providerCatalogRetry.test.js`) de propósito — o estado do catálogo é de módulo e o `node --test` roda cada arquivo no seu processo, então ele nasce limpo com os stubs de `localStorage`/`fetch` instalados ANTES do import. Ficou timing-free: 50 leituras ⇒ 1 requisição, `refresh()` ⇒ exatamente mais uma. O backoff em si é coberto por inspeção (um teste do intervalo somaria segundos de espera para pouco ganho).
- **Verificação:** `node --test web/static/js/services/*.test.js` — 298 testes puros verdes.

---

### Fase B2 — Regatear `/api/channels/providers` para o hub

**Objetivo:** o catálogo de apresentação do hub deixa de depender de uma permissão de **gestão de canais**.

**Itens:**
- `[paralelo]` `[depende de: B1]` Em [channels.py:264-273](../server/routes/channels.py#L264), trocar `permission_denied(request, "channel.manage")` (linha 272) por `conversation.reply`, seguindo o precedente das rotas vizinhas ([:51](../server/routes/channels.py#L51), [:67](../server/routes/channels.py#L67), [:80](../server/routes/channels.py#L80), [:99](../server/routes/channels.py#L99), [:125](../server/routes/channels.py#L125)) — que já são as rotas "de operador" desta mesma tela.
  - ⚠️ Verificar antes que o payload de `svc.providers(deps)` **não** carrega valor de credencial — ele descreve `credential_fields` (definição de campo), não valores. Confirmar em [channel_service.py](../app/services/channel_service.py) `providers()`/`provider_descriptor()` antes de mudar o gate.
- `[paralelo]` Teste em [tests/test_endpoints.py](../tests/test_endpoints.py): usuário com `conversation.reply` e **sem** `channel.manage` recebe 200 em `/api/channels/providers`; o payload continua sem segredo.

**Pronto quando:** `venv/bin/python -m pytest tests/ -q` verde no Postgres de teste; um operador sem `channel.manage` vê os chips de canal coloridos.

#### Status de execução — Fase B2
**Estado:** ✅ Concluída — commit `a9835d5`
- **O que foi feito:** o gate de `GET /api/channels/providers` passou de `channel.manage` para `conversation.reply`, com 4 checagens novas na suíte.
- **Como foi feito / decisões:** confirmado antes da mudança, como o plano exigia, que `svc.providers(deps)` monta o payload a partir da auto-descrição da CLASSE do provider (`credential_fields`/`config_fields` = definição de campo) e nunca lê credencial armazenada — a leitura de canal com valores é outra rota, que segue em `channel.manage`, assim como toda a escrita. O precedente seguido é o das rotas de operador vizinhas (`/connected`, `/for-filter`).
- **Problemas / pendências:** nenhuma. O papel "atendente" do catálogo tem `conversation.reply` e não tem `channel.manage`, então serve de caso de teste exato.
- **Verificação:** `venv/bin/python tests/test_endpoints.py` — **1626 passed, 0 failed**, incluindo: atendente sem `channel.manage` recebe 200 + os descriptors; o payload não traz valor de credencial; e ele continua levando 403 ao criar canal.

---

### Fase C1 — Extrair a aplicação da resposta (hoje triplicada) + testes puros

**Objetivo:** a regra "como uma resposta do servidor vira `contactData`" passa a existir **uma vez** e a ter teste automatizado.

**Itens:**
- `[sequencial]` `[depende de: A4]` Hoje o mesmo bloco — merge dos buffers de WS (`mergeBufferedMessages`) + hidratação de `_localId` nas mensagens `failed` — está copiado em três lugares: [:218-231](../web/static/js/components/contacts/hooks/useConversationSelection.js#L218) (seleção), [:264-272](../web/static/js/components/contacts/hooks/useConversationSelection.js#L264) (`reloadOpenThread`) e [:300-310](../web/static/js/components/contacts/hooks/useConversationSelection.js#L300) (`loadOlder`). Extrair para um módulo **puro** em `web/static/js/services/` (sem `preact`), no molde de [messages.js](../web/static/js/services/messages.js)/[conversationRows.js](../web/static/js/services/conversationRows.js).
- `[sequencial]` Teste `node --test` no módulo novo cobrindo: resposta obsoleta é descartada; buffer pré-fetch + durante-fetch é mesclado sem duplicar (dedup R12 + `supersedes` do plano 57); `failed` recebe `_localId`; página anterior só é prependada na thread certa.
- `[sequencial]` Os três call sites passam a chamar o módulo — **um refactor por commit**, com a suíte verde antes e depois.

**Pronto quando:** `node --test` verde no módulo novo; nenhum comportamento observável muda (é refactor puro, feito **depois** das correções para não misturar).

#### Status de execução — Fase C1
**Estado:** ✅ Concluída — commit `87706d5`
- **O que foi feito:** nasce `web/static/js/services/threadData.js` (puro) com `hydrateFailed`, `applyThreadResponse` e `prependOlder`; os três call sites passam a chamá-lo.
- **Como foi feito / decisões:** feito por último, como planejado, para não misturar refactor com correção. As funções não mutam a entrada (os call sites antigos mutavam `data.messages` in-place). Confirmação do diagnóstico: foi essa triplicação que deixou o `loadOlder` sem guarda de troca de conversa — a regra existia em três lugares e só dois recebiam atenção.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --test` — 8 testes no módulo novo (carimbo sem mutar a resposta, merge sem duplicar, hidratação das `failed`, resposta sem `messages`, prepend na ordem certa descartando o já carregado, carimbo preservado ao paginar, thread ausente) e 298 testes puros do frontend verdes.

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Abortar o request do detalhe | O `GET /api/atendimentos/{id}/messages` tem **efeito colateral**: marca a conversa como lida e as menções como lidas ([conversations.py:367-380](../server/routes/conversations.py#L367)) e agenda os recibos de leitura ([:415](../server/routes/conversations.py#L415)). Abortar pode deixar a conversa anterior como **não lida** | **Não abortar** (P1): usar só token de sequência. Se abortar virar necessário, passar `mark_read=false` no request que será descartado |
| Spinner a cada troca (A2) | Em rede rápida vira *flash* e piora a percepção | P2: exibir o estado de carga só após um limiar (~120-150ms) ou manter o cabeçalho (já correto) e trocar só o corpo por *skeleton* |
| `key=` no `<ContactDetail>` (A4) | Remontagem descarta estado interno: posição de scroll, `scrollToMsg`, menus abertos | Rascunho já é persistido em `localStorage` ([drafts.js](../web/static/js/services/drafts.js)) e sobrevive; validar scroll/`scrollToMsg` manualmente. Alternativa sem remontagem: carimbo + comparação |
| `ensureLoaded` com "desisti" (B1) | Se o primeiro fetch falhar, os chips ficam cinza até um `refresh()` ou F5 | Backoff com algumas tentativas + `refresh()` em pontos naturais (sessão estabelecida, tela Canais salva). Nunca deixar sem NENHUMA re-tentativa |
| Mudar o gate do endpoint (B2) | Expor descriptor a quem não gere canais | Confirmar que o payload não traz valor de credencial (só definição de campo); manter as rotas de escrita em `channel.manage` |
| Modo escuro | O painel de erro da A3 é tela nova | Usar `wa-*` (`bg-wa-panel`, `text-wa-text`, `border-wa-border`) e testar com o tema escuro ligado — regra do `CLAUDE.md` |
| Regressão nas curas existentes | `reloadOpenThread`/`scheduleOpenThreadResync` também gravam `contactData` | A1 já cobre os dois com o mesmo token; conferir que a reconexão de WS continua recarregando **sem** piscar o spinner (ela é de fundo, por design) |
| Um refactor por commit | A1–A4 mexem no mesmo efeito | Fases sequenciais e commits separados; suíte verde a cada fase |

---

## 6. Perguntas em aberto

**P1 — Abortar o request anterior ou só ignorar a resposta?**
✅ DECIDIDO (2026-07-28): **só ignorar** (token de sequência), sem `AbortController` nesta fase.
Contexto: a lista já aborta ([useConversationList.js:141](../web/static/js/components/contacts/hooks/useConversationList.js#L141)) porque `GET /api/contacts` é leitura pura. O detalhe **não** é: ele marca como lida ([conversations.py:367-380](../server/routes/conversations.py#L367)) e dispara recibos ([:415](../server/routes/conversations.py#L415)).
(a) Abortar — libera conexão, mas pode perder o `mark_read` da conversa recém-aberta.
(b) Só ignorar — custo de uma resposta descartada, zero efeito colateral perdido. **Recomendado e escolhido (b).**

**P2 — Como evitar o *flash* do carregamento (A2)?**
⏸️ EM ABERTO — decidir na execução, com a tela na mão.
(a) Limiar de ~120-150ms antes de exibir o estado de carga (o caso rápido não pisca).
(b) *Skeleton* no corpo mantendo o cabeçalho (que já está correto desde o clique).
Recomendação: (b), com (a) como ajuste fino se ainda incomodar.

**P3 — `key=` no `<ContactDetail>` ou carimbo + comparação (A4)?**
⏸️ EM ABERTO — depende do que a A2/A3 deixarem no caminho.
(a) `key=${selectedConvId ?? selected}`: uma linha, garantia total, mas remonta e zera estado interno.
(b) Carimbo em `contactData` + comparação no render: cirúrgico, preserva estado, um pouco mais de código.
Recomendação: (b) se A2 já eliminar a janela; (a) se sobrar qualquer caminho de vazamento.

**P4 — O catálogo de providers é mesmo o gatilho?**
⏸️ AGUARDANDO F0. Se a contagem der 1-2, B1/B2 seguem como higiene (prioridade baixa) e a causa da frequência fica em aberto — nesse caso, capturar o Network durante um travamento passa a ser o próximo passo obrigatório.

---

## 7. Apêndice — arquivos-chave

**Frontend — núcleo do bug**
- [web/static/js/components/contacts/hooks/useConversationSelection.js](../web/static/js/components/contacts/hooks/useConversationSelection.js) — efeito de carga (172-236), `reloadOpenThread` (248-274), `loadOlder` (280-312), `selectContact` (83-117)
- [web/static/js/components/contacts/Contacts.js](../web/static/js/components/contacts/Contacts.js) — derivação de `messages`/`info` (307-308) e render do detalhe (425-441)
- [web/static/js/components/contacts/ContactDetail.js](../web/static/js/components/contacts/ContactDetail.js) — `headerSubtitle` (90) × `displayName` (99)

**Frontend — catálogo de providers**
- [web/static/js/services/providerCatalog.js](../web/static/js/services/providerCatalog.js) — `ensureLoaded` (53-69), `descriptorFor` (77-81), fallback (28-31)
- [web/static/js/components/contacts/ChannelChip.js](../web/static/js/components/contacts/ChannelChip.js) — leitura no corpo de render (24-27)
- [web/static/js/components/contacts/ContactList.js](../web/static/js/components/contacts/ContactList.js) — chip sempre visível na linha (688-692)

**Frontend — padrão de referência já existente**
- [web/static/js/components/contacts/hooks/useConversationList.js](../web/static/js/components/contacts/hooks/useConversationList.js) — `fetchSeqRef`/abort (54-56, 140-144, 161, 202)
- [web/static/js/services/httpClient.js](../web/static/js/services/httpClient.js) — `request` (92-106), `handleErrorResponse` (69-76)

**Backend**
- [server/routes/conversations.py](../server/routes/conversations.py) — `GET /api/atendimentos/{conv_id}/messages` (323-437)
- [server/routes/channels.py](../server/routes/channels.py) — `GET /api/channels/providers` (264-273)

**Testes**
- [web/static/js/services/providerCatalog.test.js](../web/static/js/services/providerCatalog.test.js) — fallback do catálogo (alvo da B1)
- [tests/test_endpoints.py](../tests/test_endpoints.py) — alvo da B2

---

## 8. Checklist de verificação

Verificado por código + testes automatizados:

- [x] Resposta atrasada de outra conversa não sobrescreve a aberta; `loadOlder` não prepende na thread errada (A1) — token de sequência nos três call sites
- [x] Toda troca de conversa exibe estado de carregamento; nenhuma exibe a thread anterior (A2 + A4)
- [x] Estado de erro legível no **modo escuro** (classes `wa-*`) (A3)
- [x] Reclicar a MESMA linha re-tenta (A3) — `retryNonce` nas deps, incrementado pelo botão e pelo reclique
- [x] Impossível renderizar `contactData` de outra conversa (A4) — carimbo `_threadKey` comparado no RENDER, não no efeito
- [x] `node --test web/static/js/services/*.test.js` verde, incluindo o caso de contagem de chamadas (B1) — **298 passed**
- [x] `node --test` verde no módulo puro novo (C1) — 8 testes
- [x] `venv/bin/python tests/test_endpoints.py` verde no Postgres de teste (B2) — **1626 passed, 0 failed**
- [x] Operador sem `channel.manage` recebe 200 em `/api/channels/providers` e o payload não traz valor de credencial (B2)
- [x] `mark_read` continua funcionando: nenhum request é abortado (P1 — só o resultado é descartado)
- [x] Rascunho do compositor sobrevive à troca de conversa (A4) — a opção escolhida (carimbo) não remonta o `ContactDetail`
- [x] Sem migration nova; nenhum schema alterado
- [x] Um refactor por commit; suíte verde a cada fase — `7a31212` · `44d7c52` · `6df3340` · `3e60769` · `89212a9` · `a9835d5` · `87706d5`

Depende de execução em produção / navegador (não automatizável aqui):

- [ ] F0 registrado: chips coloridos **ou** contagem de `channels/providers` medida em produção (P4)
- [ ] Com "Slow 3G", A → B → C em cliques rápidos termina em **C**; URL e conteúdo batem (A1)
- [ ] Backend derrubado ⇒ erro visível + "Tentar de novo"; religado ⇒ botão carrega sem F5 (A3)
- [ ] Confirmar com a operadora que o painel não fica mais preso após o deploy
