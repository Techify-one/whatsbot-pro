# 04 — Respostas Rápidas (Canned Responses)

> Pesquisa de arquitetura. Feature: o atendente digita um atalho no campo de mensagem (ex.: `/oi-anna`) e ele expande para um texto pré-definido. Inclui tela de CRUD dos atalhos.
>
> Documentos relacionados: **01 (conversas)**, **02 (inboxes)**, **03 (usuários/RBAC)**.

---

## 1. O que existe hoje

**Não existe nada de respostas rápidas no WhatsBot hoje.** É feature nova, greenfield. Mas existem dois pontos de encaixe perfeitos já no código:

- **Composer (input do atendente)** — `web/static/js/components/contacts/ContactDetail.js`. O `<textarea>` está em `~linha 1290`, com `onInput=${handleInputChange}` (`~linha 233`). O texto digitado vai para o estado `input` (`useState('')`, linha 36) e é enviado por `handleSend` (`~linha 447`).
- **Autocomplete `@menção` em grupos** — já implementado no MESMO componente e é o **molde exato** para o autocomplete por `/`:
  - estado `mentionMenu` (`linha 53`): `{ query, start, index }`;
  - `updateMentionMenu` (`~linha 205`) faz `val.slice(0, pos).match(/(?:^|\s)@([\p{L}\p{N}_]*)$/u)` para detectar o token sob o cursor;
  - `applyMention` (`~linha 212`) substitui `input.slice(0, start) + insert + input.slice(pos)` e reposiciona o caret;
  - navegação por teclado (↑/↓/Enter/Esc) já tratada no `onKeyDown` (`~linha 385`);
  - dropdown renderizado em `~linha 1265`.

> **A expansão de `/atalho` deve reusar exatamente esse padrão** — basta um segundo "menu" (`quickReplyMenu`) com gatilho `/` em vez de `@`.

**Envio**: mensagens do operador passam pelo backend (`sendMessage` em `web/static/js/services/api.js`) e vão ao GOWA via `/send/message`. A expansão do atalho NÃO precisa tocar o caminho de envio se for resolvida no front (ver §5).

---

## 2. Requisitos

**Funcionais**
- CRUD de respostas rápidas: `short_code` (atalho/trigger) + `content` (texto).
- No composer, ao digitar `/`, abrir autocomplete filtrando por `short_code` (e idealmente por trecho do conteúdo).
- Selecionar um item (clique ou Enter) expande o `/atalho` no `content` no campo de texto — **sem enviar automaticamente** (o atendente revisa antes).
- Tela de gestão acessível conforme RBAC (doc 03).
- (Opcional MVP+) substituição de variáveis `{{...}}` no momento da expansão.

**Não-funcionais**
- Reaproveitar o runtime atual: Preact + HTM sem build, SQLAlchemy Core + Alembic, FastAPI.
- Funcionar nos dois temas (claro/escuro) — usar classes `wa-*` e `.wa-field` (regra do CLAUDE.md).
- Escopo coerente com inboxes (doc 02) e usuários (doc 03).

---

## 3. Modelo de dados — tabela `quick_replies`

Espelha o modelo do Chatwoot (`short_code`, `content`, `account_id`), adaptado para o domínio do WhatsBot (inboxes + usuários dos docs 02/03).

```sql
-- DDL ilustrativo (Alembic / SQLAlchemy Core)
CREATE TABLE quick_replies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,  -- SERIAL no Postgres
    short_code  TEXT    NOT NULL,                   -- atalho SEM a barra: "oi-anna"
    content     TEXT    NOT NULL,                   -- pode conter {{placeholders}}

    -- Escopo (exatamente UM nível ativo por linha):
    scope       TEXT    NOT NULL DEFAULT 'global',  -- 'global' | 'inbox' | 'user'
    inbox_id    INTEGER REFERENCES inboxes(id) ON DELETE CASCADE,   -- doc 02; NULL se não-inbox
    user_id     INTEGER REFERENCES users(id)   ON DELETE CASCADE,   -- doc 03; NULL se não-user

    created_by  INTEGER REFERENCES users(id),       -- doc 03 (auditoria)
    created_at  TEXT    NOT NULL,                    -- ISO8601 (TIMESTAMP no PG)
    updated_at  TEXT    NOT NULL
);

-- Unicidade do short_code POR escopo (ver discussão abaixo):
CREATE UNIQUE INDEX ux_qr_global ON quick_replies (short_code)
    WHERE scope = 'global';
CREATE UNIQUE INDEX ux_qr_inbox  ON quick_replies (inbox_id, short_code)
    WHERE scope = 'inbox';
CREATE UNIQUE INDEX ux_qr_user   ON quick_replies (user_id, short_code)
    WHERE scope = 'user';

CREATE INDEX ix_qr_short_code ON quick_replies (short_code);
```

> Índices parciais (`WHERE ...`) funcionam em SQLite e Postgres. Como o WhatsBot suporta os dois backends, se quiser evitar índice parcial dá para usar um único `UNIQUE (scope, COALESCE(inbox_id,0), COALESCE(user_id,0), short_code)` via coluna gerada — mas índice parcial é mais limpo.

### Discussão: unicidade do `short_code` e escopo

- **Chatwoot** mantém o `short_code` **único por conta** (campo simples, sem escopo por inbox/usuário no modelo base). É o caminho mais simples — equivalente a usar só `scope='global'` aqui.
- **WhatsBot precisa de escopo** porque já terá inboxes (doc 02) e usuários (doc 03). Três níveis propostos:
  - `global` — disponível para todos os atendentes em todas as inboxes (ex.: política de devolução).
  - `inbox` — só aparece quando a conversa pertence àquela inbox (doc 01/02) (ex.: assinatura específica de um número/setor).
  - `user` — atalhos pessoais do atendente (ex.: `/oi-anna` da Anna).
- **Resolução em caso de colisão de `short_code` entre escopos** — precedência sugerida: **user > inbox > global** (o mais específico vence; o pessoal sobrescreve o global). A consulta de autocomplete junta os três escopos visíveis ao atendente atual e, se houver `short_code` igual, mostra o de maior precedência (ou mostra todos rotulando o escopo).
- **`short_code` armazenado sem a barra** (`oi-anna`); a `/` é só o gatilho da UI. Validar formato (ex.: `^[a-z0-9][a-z0-9_-]{1,}$`) para casar com o regex de detecção no composer.

---

## 4. Substituição de variáveis (opcional no MVP)

Padrão consagrado (Chatwoot, Zendesk, Intercom): **`{{placeholder}}` com chaves duplas**, substituídas no momento de inserir/enviar. Se a variável não tem valor, vira string vazia.

### Catálogo de placeholders proposto (alinhado ao WhatsBot)

| Placeholder | Fonte no WhatsBot | Observação |
|---|---|---|
| `{{contact.name}}` | `contacts.name` | nome do contato |
| `{{contact.first_name}}` | derivado de `contacts.name` (1º token) | comum em Chatwoot/Zendesk |
| `{{contact.phone}}` | `contacts.phone` | |
| `{{contact.email}}` | `contacts.email` | |
| `{{contact.company}}` | `contacts.company` | já existe na tabela |
| `{{agent.name}}` | usuário logado (doc 03) | quem está atendendo |
| `{{agent.first_name}}` | derivado | |
| `{{inbox.name}}` | inbox da conversa (doc 02) | |

> Mantém compatibilidade conceitual com o catálogo do Chatwoot (`{{contact.name}}`, `{{contact.first_name}}`, `{{agent.name}}`, `{{inbox.name}}`, etc.).

### Como resolver
- **Onde**: na expansão (front-end), montar um dicionário de contexto a partir dos dados já disponíveis no `ContactDetail` (`contact`, `info`) + identidade do atendente (doc 03) e fazer um `replace` simples por regex `\{\{\s*([\w.]+)\s*\}\}`.
- **Fallback**: chave desconhecida ou sem valor → string vazia (comportamento do Chatwoot/Zendesk).
- Como o atendente sempre revisa o texto antes de enviar, resolver no front é suficiente e mais simples. Se no futuro houver envio automático/server-side, a mesma tabela de substituição roda no backend antes do `/send/message`.

---

## 5. Onde a expansão acontece — client-side vs server-side

| Abordagem | Como | Prós | Contras |
|---|---|---|---|
| **Client-side (composer)** | Autocomplete por `/` no `ContactDetail.js`; ao selecionar, troca `/atalho` pelo `content` (com variáveis já resolvidas) no `<textarea>` | Atendente **vê e edita** o texto antes de enviar; UX igual Chatwoot/Slack; zero mudança no caminho de envio | Lógica de variáveis no front; precisa carregar a lista de atalhos no client |
| **Server-side (no envio)** | Operador manda `/atalho` cru; backend detecta e interpola antes do GOWA | Fonte única de verdade; funciona p/ canais sem UI | Atendente **não revisa** o resultado; ambíguo (e se o texto começar com `/` legítimo?); difícil cancelar |

### Recomendação

**Autocomplete client-side no composer + fonte no banco.** É o modelo do Chatwoot ("digite `/` seguido do shortcode") e do Slack ("digite `/` e veja o autocomplete"). O texto é expandido localmente para o atendente revisar/ajustar, e o envio segue o caminho atual (`sendMessage` → backend → GOWA) **sem modificação**. O banco é a fonte; o front busca os atalhos via `GET /api/quick-replies`.

A interpolação de variáveis também roda no front (no momento da expansão). Server-side fica reservado a um caso futuro de automação.

---

## 6. Impacto no frontend

**`ContactDetail.js` (composer)** — clonar o mecanismo de `@menção`:
1. Novo estado `quickReplyMenu` (`{ query, start, index }`), análogo a `mentionMenu`.
2. Em `updateMentionMenu` (ou função irmã), adicionar detecção de `/`: regex tipo `/(?:^|\s)\/([\w-]*)$/` sobre `val.slice(0, pos)`. Atenção para **não conflitar** com mensagens que legitimamente começam com `/` — só abrir o menu quando houver match e houver atalhos; Esc fecha.
3. Carregar a lista de atalhos (uma vez por conversa) filtrada por escopo visível (global + inbox da conversa + user logado).
4. `applyQuickReply(cand)` — reusar a lógica de `applyMention`: substituir o token `/atalho` por `resolveVariables(cand.content, ctx)` e reposicionar o caret. **Não** enviar automaticamente.
5. Dropdown renderizado com as mesmas classes do menu de menção (mostrar `short_code`, preview do `content`, e rótulo do escopo). Navegação ↑/↓/Enter/Esc já existe no `onKeyDown` (`~linha 385`) — estender para o novo menu.

**Tela de CRUD de respostas rápidas** — novo componente Preact (ex.: `web/static/js/components/QuickReplies.js`), acessível via menu (GearMenu ou navegação principal):
- Lista com `short_code`, preview do `content`, escopo, autor (`created_by`).
- Form de criar/editar: campo `short_code` (validado), `content` (textarea), seletor de escopo (global/inbox/user) — com as opções de escopo gateadas por RBAC (doc 03).
- Preview de placeholders (mostrar como ficaria substituído com dados de exemplo).
- Botão de excluir.
- **Acesso (RBAC, doc 03)**: ADM gerencia atalhos `global` e `inbox`; atendente gerencia os próprios `user` (e, se a política permitir, sugere globais). Ver §9.

**Tema**: usar `wa-*` e `.wa-field` (regra do CLAUDE.md) — dropdown e form precisam ser legíveis no modo escuro.

---

## 7. Impacto no backend

Novas rotas REST sob o padrão `{"ok", "data", "error"}` já usado:

| Método | Endpoint | Descrição | Permissão (doc 03) |
|---|---|---|---|
| GET | `/api/quick-replies` | Lista atalhos **visíveis ao usuário** (global + inbox da conversa + próprios). Aceita `?inbox_id=` para filtrar pelo contexto da conversa | Atendente |
| POST | `/api/quick-replies` | Cria atalho (`short_code`, `content`, `scope`, `inbox_id?`, `user_id?`) | Conforme escopo (ver §9) |
| PUT | `/api/quick-replies/{id}` | Edita | Dono / ADM conforme escopo |
| DELETE | `/api/quick-replies/{id}` | Remove | Dono / ADM conforme escopo |

**Camada de dados**: repositório novo `db/repositories/quick_reply_repo.py` (SQLAlchemy Core, `with get_engine().connect()/begin()`), tabela `quick_replies` em `db/tables.py`, migration Alembic. Repos chamados das rotas via `asyncio.to_thread`.

**Validação**:
- `short_code` formato + unicidade por escopo (capturar violação de índice único e retornar erro amigável).
- Coerência `scope`↔`inbox_id`/`user_id` (ex.: `scope='inbox'` exige `inbox_id`).
- Autorização por escopo (doc 03): rejeitar criação de `global`/`inbox` por atendente sem permissão.

**`created_by`** preenchido com o usuário autenticado (doc 03), para auditoria e para o filtro de atalhos `user`.

---

## 8. Faseamento / MVP

1. **Fase 1 — texto puro + escopo global**
   Tabela `quick_replies` (só `scope='global'`), CRUD básico, autocomplete por `/` no composer expandindo `content` literal. Entrega o caso `/oi-anna` imediatamente. (Equivale ao modelo Chatwoot base.)
2. **Fase 2 — escopo por inbox e por usuário**
   Adicionar `scope`/`inbox_id`/`user_id`, precedência user>inbox>global, RBAC na tela e nas rotas (doc 02/03).
3. **Fase 3 — variáveis `{{...}}`**
   Catálogo (§4) + resolução no front + preview na tela de CRUD.
4. **Fase 4 (futuro) — mídia/anexos**
   Atalho que insere imagem/documento/áudio pré-definido (ver pergunta em aberto). Mais complexo: precisa armazenar referência de mídia e casar com `sendImage/sendDocument/sendAudio` já existentes.

---

## 9. Perguntas em aberto

1. **Escopo padrão**: começar só com `global` (mais simples) ou já entregar os três escopos? (depende de quão pronto está o doc 02/03 na implementação).
2. **Quem cria o quê** (RBAC, doc 03): atendente pode criar atalhos `global`/`inbox` ou só pessoais (`user`)? Sugestão: ADM cria global/inbox; atendente só os próprios.
3. **Precedência em colisão de `short_code`** entre escopos: confirmar `user > inbox > global` (ou mostrar todos rotulando o escopo no dropdown).
4. **Suporta mídia/anexos** ou só texto no curto prazo? (recomendo texto primeiro — Fase 1–3).
5. **Variáveis**: catálogo final (§4) e o que fazer com placeholder sem valor — string vazia (padrão Chatwoot/Zendesk) ou aviso no preview?
6. **Conflito do gatilho `/`** com mensagens que começam legitimamente com barra — abrir o menu só com match + atalhos existentes, e permitir Esc; suficiente?
7. **Carregamento da lista** no composer: buscar a cada abertura de conversa, ou cachear no client e invalidar ao salvar na tela de CRUD?

---

## 10. Referências

**Chatwoot — Canned Responses**
- Add a New Canned Response (API: `short_code`, `content`, `account_id`) — https://developers.chatwoot.com/api-reference/canned-responses/add-a-new-canned-response
- List all Canned Responses — https://developers.chatwoot.com/api-reference/canned-responses/list-all-canned-responses-in-an-account
- Feature page (gatilho `/` + shortcode) — https://www.chatwoot.com/features/canned-responses/
- How to create saved reply templates — https://www.chatwoot.com/hc/user-guide/articles/1677501325-how-to-create-saved-reply-templates-with-canned-responses
- Template variables (catálogo `{{contact.name}}`, `{{agent.name}}`, `{{inbox.name}}`, custom attributes) — https://www.chatwoot.com/docs/user-guide/features/template-variables
- Issue: Support variables in canned response — https://github.com/chatwoot/chatwoot/issues/1886
- Issue: Add variable input placeholders — https://github.com/chatwoot/chatwoot/issues/7173
- Schema de referência (DrawSQL) — https://drawsql.app/templates/chatwoot

**Zendesk — Macros & Placeholders**
- Creating macros for repetitive responses — https://support.zendesk.com/hc/en-us/articles/4408844187034-Creating-macros-for-repetitive-ticket-responses-and-actions
- Using placeholders (`{{...}}`) — https://support.zendesk.com/hc/en-us/articles/4408887218330-Using-placeholders
- Guia de placeholders (SwiftEQ) — https://swifteq.com/post/zendesk-placeholders

**Intercom — Macros / Saved Replies**
- Creating and managing macros — https://www.intercom.com/help/en/articles/6433193-creating-and-managing-macros

**UI de autocomplete por `/`**
- Slack — Implementing slash commands (composer `/` + autocomplete) — https://docs.slack.dev/interactivity/implementing-slash-commands/
- Slack-like autocomplete (emojis e slash) — Algolia — https://www.algolia.com/developers/code-exchange/slack-like-autocomplete-for-emojis-and-slash-commands
- Chatwoot keyboard shortcuts / command bar — https://www.chatwoot.com/features/keyboard-shortcuts/
