# Plano 49 — Plugin "utm_atendente" (UTM por atendente nos links de venda)

> **Status:** PLANEJAMENTO · **Data:** 2026-07-13 · **Escopo:** médio (plugin novo, 100% self-contained; **zero** mudança no core)
> **Origem:** pedido do usuário — portar do Nexus (`/opt/nexus/gerenciamento-ia/ai/src/services/utm_replacer.py`) a substituição de `utm_term` por atendente. Quando um atendente humano "cutuca" a IA por nota privada, os links de venda que a IA envia recebem a UTM daquele atendente (comissionamento).
> **Método:** leitura do código real do Nexus (origem) + do WhatsBot (destino) + 6 sub-agentes de exploração em paralelo. Todo `arquivo:linha` abaixo foi **verificado** no working tree.
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.
> **⚠️ Execução em PARALELO:** outra IA está implementando o **Plano 48** (plugin `retorno_automatico`) neste mesmo repositório ao mesmo tempo. Toque **somente** em `storages/plugins/utm_atendente/**`, `tests/test_utm_atendente.py` e os blocos de status deste arquivo. **Zero** edição no core. Use um banco de teste próprio (`whatsbot_test_49`), rode testes por arquivo e, se subir o servidor, use a porta 8149.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano | Data |
|---|---------|----------------------|------|
| D1 | **Gatilho = só nota privada HUMANA.** Uma linha `role='private_note'` com `sent_by_user_id` **NÃO-NULO** entre as últimas N mensagens da conversa. Notas geradas pela própria IA ou por plugins têm `sent_by_*` NULL e são **ignoradas**. | O discriminador humano-vs-máquina é `sent_by_user_id IS NOT NULL`. Ver §2.4. ✅ | 2026-07-13 |
| D2 | **Identidade do atendente = `users.id`.** A tela do plugin escolhe o atendente de uma lista de usuários do WhatsBot e associa um `utm_term` a cada `users.id`. Casamento por id (estável a rename), não por nome. | Mapa `{ user_id → utm_term }`. Requer instalação **multiusuário** (ver R6/P2 sobre single-password). ✅ | 2026-07-13 |
| D3 | **Cobertura = TODA resposta pública da IA.** Inclui o fluxo automático (webhook) **e** o fluxo "IA lê" (atendente força a IA via nota privada → `_run_private_ai`). | Hook = **`filter.reply.parts`** (plural) com guarda de `source`. Ver §2.2/§2.3. ✅ | 2026-07-13 |
| D4 | **NUNCA aplicar em mensagem manual do atendente.** | Garantido **por construção** pelo hook: o envio manual do operador passa por `filter.reply.part` (singular, `source='operator'` em `contacts.py:747`), **não** pelo `.parts` (plural). Ver §2.3. ✅ | 2026-07-13 |
| D5 | **Link de venda casado por REGEX configurável.** Padrão `^https?://exemplo\.cc/` (o usuário edita na tela quando mudar). | O matcher de link-de-venda é uma regex compilada da config, não uma lista de prefixos como no Nexus. Só URLs que casam a regex são reescritas. ✅ | 2026-07-13 |
| D6 | **Formato UTM configurável**, defaults `param='utm_term'` e `base='ia'`. Regra: `utm_term=ia` → `utm_term=<valor mapeado>` (ex. `ia-atendente`); anexa se faltar; deixa intacto se já houver **outro** `utm_term`. | Nome do parâmetro e valor-base (alvo do match) editáveis na tela. O valor mapeado por atendente é o `utm_term` completo (default sugerido `ia-<slug>`). ✅ | 2026-07-13 |
| P0 | **Princípio fixo:** plugin 100% self-contained em `storages/plugins/utm_atendente/` — **zero edição no core**. Só **lê** tabelas core (via repos e/ou `SELECT` read-only) e reescreve texto via filtro. Distribuição por `.zip` (Importar na tela Plugins). | Nenhum arquivo fora de `storages/plugins/utm_atendente/` é tocado. Ver [[plugin-changes-distributed-via-zip]]. | 2026-07-13 |
| P1 | **Fail-open absoluto.** O filtro NUNCA retorna `None` (isso abortaria o envio) e NUNCA levanta: qualquer erro/dúvida ⇒ devolve as `parts` intactas. | Todo o corpo do filtro é `try/except` que retorna `value` no fim e no `except`. Ver §4.1. | 2026-07-13 |

---

## 1. Resumo executivo

**Problema.** No Nexus, quando um atendente humano manda uma **nota privada** para a IA continuar o atendimento, o link de venda que a IA envia precisa levar a **UTM daquele atendente** (`utm_term=ia-atendente` em vez de `utm_term=ia`) para atribuir a comissão. O usuário quer o mesmo no WhatsBot, **como plugin**.

**Solução.** Um plugin `utm_atendente` que registra o filtro **`filter.reply.parts`** (a lista de partes da resposta da IA, antes do envio ao WhatsApp). O filtro: (1) confirma que é resposta pública da IA (guarda por `source`); (2) resolve a conversa pelo `phone`; (3) lê as últimas **N** mensagens (N configurável, default 5) por `SELECT` read-only; (4) acha a **nota privada de humano mais recente** cujo `sent_by_user_id` está no mapa `{ user_id → utm_term }`; (5) reescreve os links que casam a **regex de venda** (`utm_term=ia` → `utm_term=ia-atendente`, ou anexa). Fail-open em todo passo.

**Insight arquitetural.** O WhatsBot já grava **quem** (`sent_by_user_id`/`sent_by_name`) escreveu cada nota privada de operador (`contacts.py:1282-1285`, do `current_user()`) — é o análogo exato do `messages.sender_id` do Chatwoot. Logo o "pino" da feature (saber qual atendente cutucou a IA) existe nos dados, e o plugin **não precisa de tabela nem migration** para o gatilho — só um mapa em config para os `utm_term`.

---

## 2. Como funciona hoje (mapa do que vamos reaproveitar)

### 2.1 A feature na origem (Nexus/Chatwoot) — o que estamos portando

`/opt/nexus/gerenciamento-ia/ai/src/services/utm_replacer.py` + `response_dispatcher.py` + `database.py`.

| Peça no Nexus | Arquivo:linha (Nexus) | O que faz |
|---|---|---|
| Gancho no envio público | `response_dispatcher.py:74-75` | `if not response.private_message: msg = await maybe_replace_utm_term(conversation_id, msg)` — só em mensagem pública |
| Núcleo | `utm_replacer.py:125-177` `maybe_replace_utm_term` | fast-path (tem `http`? tem prefixo de venda?), carrega mapa, acha 1º agente com nota privada nas últimas 5, reescreve |
| Regex | `utm_replacer.py:26,29` | `_UTM_TERM_IA_PATTERN = ([?&])utm_term=ia(?=[&\s)\]\n]\|$)` · `_URL_PATTERN = https?://[^\s)\]]+` |
| Reescrita por URL | `utm_replacer.py:101-122` `_apply_utm_term` | só em URL de venda: tem `utm_term=ia`→substitui; tem outro `utm_term`→deixa; sem→anexa |
| Gatilho (nota humana) | `database.py:320-337` `get_recent_private_agent_ids` | `SELECT DISTINCT sender_id ... WHERE private=true AND sender_type='User'` nas últimas N |
| Mapa `{agente→utm}` | `database.py:292-297` | categoria `LIST_UTM_TERM_SUBISTITUICAO`, JSON `{id_agente, utm_term}` |
| Prefixos de venda | `database.py:300-305` | variável `links_vendas` |

**Diferenças no port (por decisão):** prefixo→**regex** (D5); `sender_id` Chatwoot→**`sent_by_user_id`** WhatsBot; cobre também o fluxo "IA lê" (D3); param/base configuráveis (D6).

### 2.2 Onde enganchar no WhatsBot — os 3 filtros de reply de saída

Pipeline em `app/services/messaging_service.py` (resposta automática da IA) e `server/routes/contacts.py` (fluxo "IA lê"):

| Filtro | Valor | Call sites (verificados) | Dispara para |
|---|---|---|---|
| `filter.reply.raw` | `str` (resposta inteira, antes do split) | `messaging_service.py:328` `{phone}` | **só** IA webhook normal |
| **`filter.reply.parts`** | `list[str]` (partes já splitadas) | `messaging_service.py:345` `{phone}` · `contacts.py:1093` `{phone, source}` | **IA webhook normal + IA-lê pública** |
| `filter.reply.part` | `str` (cada parte) | `messaging_service.py:358` · `contacts.py:1099` · **`contacts.py:747` `{source:'operator'}`** | IA + IA-lê + **envio manual do operador** |

> ⚠️ **Por que `filter.reply.parts` (plural) e não os outros** (D3+D4): é o **único** que cobre os DOIS caminhos públicos da IA **e** exclui o envio manual do operador. O `.raw` perde o fluxo "IA lê" (que só emite `.parts`/`.part`, nunca `.raw`). O `.part` singular inclui `contacts.py:747` (envio manual, `source='operator'`) — usá-lo violaria D4. **Confirmado por grep exaustivo dos call sites.**

### 2.3 O `source` no `ctx.extras` — a guarda de cobertura

| Origem da resposta | `ctx.extras` em `filter.reply.parts` | Ação do plugin |
|---|---|---|
| IA automática (webhook) | `{"phone": ...}` (sem `source`) | **aplicar** |
| IA-lê, resposta pública (`reply_in_chat=True`) | `{"phone":..., "source":"private_ai"}` (`contacts.py:1091,1093`) | **aplicar** |
| IA-lê, vira nota privada (`reply_in_chat=False`) | `{"phone":..., "source":"private_ai_note"}` | **pular** (não vai ao WhatsApp) |
| Envio manual do operador | *(não dispara `.parts`)* | n/a — nunca chega aqui |

Regra: aplicar quando `source` é `None` **ou** `'private_ai'`; pular quando `source == 'private_ai_note'`.

### 2.4 O gatilho: nota privada de humano com autor gravado

| Fato | Arquivo:linha (verificado) |
|---|---|
| Endpoint que salva a nota privada do operador COM autor | `server/routes/contacts.py:1274` `_u = current_user(request)` → `:1282-1285` `add_message("private_note", text, sent_by_user_id=(_u.get("id")...), sent_by_name=(_u.get("name")...))` |
| Colunas de autor na tabela `messages` | `db/tables.py:129` `Column("sent_by_user_id", Integer)` (FK **lógica** p/ `users.id`, sem constraint) · `:130` `Column("sent_by_name", Text)` (snapshot) |
| Nota da **IA** (fluxo IA-lê, `reply_in_chat=False`) — SEM autor | `contacts.py:1116` `contact.add_message("private_note", p)` (sem `sent_by_*`) |
| Nota de **plugin** (ex. protocolos) — SEM autor | `assets/plugin_examples/protocolos/logic.py` `cm.add_message("private_note", text_p)` |
| Tabela `users` (id/name/email) | `db/tables.py:278-294` |

⇒ **`role='private_note' AND sent_by_user_id IS NOT NULL`** isola exatamente "humano cutucou". (D1)

### 2.5 Leitura das últimas N — e o gotcha do read path

| Fato | Arquivo:linha |
|---|---|
| `get_context_by_conversation(conversation_id, limit, *, exclude=None)` — últimas N por conversa; **NÃO** exclui `private_note` | `db/repositories/message_repo.py:150` · exclusão em `:164` = `("transcription","tool_call","system_notice","conversation_event","system","error")` |
| ⚠️ **`_row_to_dict` OMITE `sent_by_user_id`** (só expõe `sent_by_name`) | `message_repo.py:454-482` — comentário `:480` "o `sent_by_user_id` é interno e não vai ao cliente" |
| ⇒ para casar por `users.id` (D2), o plugin faz **`SELECT` cru** | `SELECT role, content, sent_by_user_id, sent_by_name, ts FROM messages WHERE conversation_id=:cid ORDER BY ts DESC LIMIT :n` |
| Acesso a DB de dentro de filtro | `plugins/context.py:174` `make_plugin_db()` = `get_engine().begin()`; padrão de query em `storages/plugins/vendas_ia/state.py` (`from sqlalchemy import text` + `with make_plugin_db() as conn: conn.execute(text(...)).mappings()`) |

### 2.6 Resolver a conversa a partir do `phone` (o filtro só recebe `phone`)

| Passo | Arquivo:linha |
|---|---|
| Contato por telefone | `db/repositories/contact_repo.py:87` `get_by_phone(phone)` |
| Conversa aberta (escopada) | `db/repositories/conversation_repo.py:259` `get_open_for_contact_scoped(contact)` · `:207` `get_open_for_contact(contact_id)` · `:219` `get_latest_for_contact(contact_id)` (fallback) |
| Padrão pronto (resolver conversa dentro de um filtro) | `storages/plugins/vendas_ia/filters.py` — `ContactMemory(phone, channel_id)` + `conversation_repo.get_open_for_contact_scoped(contact)` |

⚠️ O `extras` do `.parts` **não** traz `channel_id` — resolução é **best-effort** (conversa aberta mais recente do phone). Ver **R3/P1**.

### 2.7 Infra de config e UI de plugin (core, já pronta)

| Recurso | Arquivo:linha | Uso |
|---|---|---|
| Registro de filtros do plugin | `plugins/events.py:496` `register_plugin_filters` | `FILTERS = {"filter.reply.parts": (fn, priority)}` |
| `apply_filter` isola exceção (fail-open no core) | `plugins/events.py:514,558` | exceção do filtro logada e valor passa adiante |
| `FilterContext` (handler/plugin_id/plugin_db/extras) | `plugins/context.py:385-401` | `ctx.extras.get("phone")`, `ctx.plugin_db` |
| Settings declarativas GET/PUT | `server/routes/plugins.py:284-326` | persiste `plugin.<id>.<campo>` |
| Config JSON auto-encode | `db/repositories/config_repo.py:17,42` | `set(k, obj)` faz `json.dumps`; `get(k)` faz `json.loads` |
| Screen `config:true` (modal Configurar) | `plugins/manifest.py:184-198` | Preact custom **substitui** o form declarativo no modal |
| Router do plugin | `server/routes/plugins.py` monta `entry.routes` em `/api/plugins/<id>` | endpoints próprios (lista de usuários, CRUD do mapa) |
| Lista de usuários (core) | `server/routes/users.py:41` `GET /api/users` (gated `users.manage`) · `db/repositories/user_repo.py:46` `list_all()` | dropdown de atendentes |
| RBAC de plugin | `plugins/context.py` `plugin_permission("<key>")` · `plugin.yaml` bloco `rbac:` | gate da tela/rotas |

**Plugin-referência LEGÍVEL no repo (copiar estrutura):** `assets/plugin_examples/protocolos/` — tem `settings.py` + screen `config:true` + `routes.py` + `migrations/` + `static/config.js` (o mais completo). Secundário: `assets/plugin_examples/telegram/`.

### 2.8 Falsos positivos descartados (com a razão)

| Ideia que parece certa | Por que está DESCARTADA |
|---|---|
| Enganchar em `filter.reply.raw` | Perde o fluxo "IA lê" (`_run_private_ai` só emite `.parts`/`.part`) — viola D3. |
| Enganchar em `filter.reply.part` (singular) | Dispara também no envio manual do operador (`contacts.py:747`) — viola D4. |
| Ler as notas via `message_repo.get_context*` | `_row_to_dict` **remove** `sent_by_user_id` (`message_repo.py:480`) — impossível casar por id (D2). Precisa de `SELECT` cru. |
| Casar o atendente por `sent_by_name` | Snapshot mutável; colide com homônimos. D2 fixou `users.id`. (`sent_by_name` fica só como rótulo de exibição.) |
| Criar tabela `plugin_utm_atendente_map` para o mapa | Desnecessário para um mapa pequeno; JSON em `plugin.utm_atendente.utm_mapping` basta e evita migration. Tabela fica como alternativa em P3. |
| Pedir `channel_id` no `extras` do filtro | Exigiria mudar o core (viola P0). Aceitamos resolução best-effort por phone (R3). |
| `lookback` por-canal (como `max_context_messages`) | O gatilho é uma nota privada numa conversa específica; N é um número simples do plugin. Por-canal adicionaria acoplamento sem ganho. |

---

## 3. Inventário — estrutura do plugin

Todos em `storages/plugins/utm_atendente/` (id `utm_atendente`, regex-válido; prefixo de tabela `plugin_utm_atendente_` **não usado** — sem migration).

| Arquivo | Papel | Risco | Esforço |
|---|---|---|---|
| `plugin.yaml` | manifest: `entry.filters`, `entry.routes`, `entry.settings`, `screens[config:true]`, `rbac` | baixo | S |
| `__init__.py` | vazio (pacote) | baixo | S |
| `settings.py` | `class Settings(BaseModel)`: `enabled`, `lookback_messages`, `utm_param`, `utm_base`, `sales_link_regex` (todos escalares, auto-form) | baixo | S |
| `utm.py` | **puro**: `apply_utm(text, term, *, param, base, sales_re) -> str` (port do `_apply_utm_term`) + `has_sales_link(text, sales_re)` | médio | M |
| `selection.py` | resolve conversa por phone + `SELECT` cru das últimas N + escolhe `utm_term` do atendente humano mais recente (lê o mapa) | médio | M |
| `config_store.py` | wrappers `get_settings()` / `load_mapping()` / `save_mapping()` sobre `config_repo` (chaves `plugin.utm_atendente.*`), com cache TTL curto opcional | baixo | S |
| `filters.py` | `FILTERS = {"filter.reply.parts": (rewrite_utm, 90)}`; orquestra guarda de `source` → selection → utm; fail-open | médio | M |
| `routes.py` | `router = APIRouter()`: `GET /users` (lista atendentes), `GET/PUT /mapping` (CRUD do mapa), gated `plugin_permission("config")` | baixo | M |
| `static/utm_atendente.js` | screen `config:true`: seção "Geral" (N, param, base, regex, enabled) + "Mapeamento" (dropdown de usuário → `utm_term`, lista/editar/remover). Dark-mode `wa-*`/`.wa-field` | médio | L |
| *(dev)* `tests/test_utm_atendente.py` (no repo core) | unit dos puros + integração da seleção | baixo | M |

---

## 4. Lógica detalhada

### 4.1 `filters.py` — o orquestrador (fail-open, D1–D6)

Assinatura: `def rewrite_utm(ctx, parts):` (registrada como `("filter.reply.parts", 90)`; sync — o core roda via `apply_filter`). Esqueleto conceitual:

```
try:
    cfg = get_settings()
    if not cfg.enabled: return parts
    if not isinstance(parts, list) or not parts: return parts
    src = (ctx.extras or {}).get("source")
    if src == "private_ai_note": return parts          # D3 — não vai ao WhatsApp
    # src is None (IA normal) OU "private_ai" → prosseguir
    sales_re = compile(cfg.sales_link_regex)            # cacheado; regex inválida ⇒ return parts
    if not any(has_sales_link(p, sales_re) for p in parts): return parts   # fast-path
    phone = (ctx.extras or {}).get("phone")
    if not phone: return parts
    term = select_term_for_phone(phone, cfg.lookback_messages)   # selection.py; None se ninguém casa
    if not term: return parts
    return [apply_utm(p, term, param=cfg.utm_param, base=cfg.utm_base, sales_re=sales_re) for p in parts]
except Exception:
    logger.warning("utm_atendente: rewrite falhou, passando parts intactas", exc_info=True)
    return parts
```

> **NUNCA `return None`** (abortaria o envio — `messaging_service.py:346`, `contacts.py:1094`). Em qualquer dúvida, devolve `parts`.

### 4.2 `selection.py` — quem é o atendente (D1/D2)

```
select_term_for_phone(phone, n):
  contact = contact_repo.get_by_phone(phone)               # None ⇒ return None
  conv = conversation_repo.get_open_for_contact(contact["id"]) \
         or conversation_repo.get_latest_for_contact(contact["id"])   # best-effort (R3)
  if not conv: return None
  mapping = load_mapping()                                  # { "15": "ia-atendente", ... }  (str(user_id) → utm_term)
  if not mapping: return None
  rows = SELECT role, sent_by_user_id, ts
         FROM messages WHERE conversation_id=:cid
         ORDER BY ts DESC LIMIT :n                          # já DESC = mais recente primeiro
  for r in rows:                                            # precedência: nota humana MAIS RECENTE vence
      if r.role == 'private_note' and r.sent_by_user_id is not None:
          t = mapping.get(str(r.sent_by_user_id))
          if t: return t
  return None
```

- Query crua via `ctx.plugin_db()`/`make_plugin_db()` + `text()` (§2.5) — **necessária** porque o repo remove `sent_by_user_id`.
- Opcional (detalhe): `if contact.get("is_group"): return None` — venda é 1:1 (como `vendas_ia`).

### 4.3 `utm.py` — a reescrita (puro, port do Nexus + D5/D6)

```
_URL = re.compile(r"https?://[^\s)\]]+")

apply_utm(text, term, *, param, base, sales_re):
  def repl(m):
     url = m.group(0)
     if not sales_re.search(url): return url               # D5 — só links de venda
     base_pat = re.compile(rf"([?&]){re.escape(param)}={re.escape(base)}(?=[&\s)\]\n]|$)")
     if base_pat.search(url):                               # utm_term=ia → utm_term=<term>
         return base_pat.sub(rf"\1{param}={term}", url)
     if f"{param}=" in url: return url                      # já tem OUTRO utm_term → intacto
     sep = "&" if "?" in url else "?"                       # sem o param → anexa
     return f"{url}{sep}{param}={term}"
  return _URL.sub(repl, text)
```

- **Idempotência:** após virar `utm_term=ia-atendente`, o lookahead `(?=[&\s)\]\n]|$)` falha em `ia-` (vem `-`), e o ramo "anexar" vê `utm_term=` presente ⇒ intacto. Reprocessar é no-op. (D6)
- **`term`** = valor do mapa (o `utm_term` completo, ex. `ia-atendente`). A tela pré-preenche `<base>-` ao adicionar atendente (ver §5). `base` só é o **alvo do match**.

### 4.4 Config (`config_store.py`) — chaves e defaults

| Chave (`plugin.utm_atendente.*`) | Tipo | Default | Onde edita |
|---|---|---|---|
| `enabled` | bool | `true` | screen (Geral) |
| `lookback_messages` | int | `5` | screen (Geral) — "campo fácil de trocar" |
| `utm_param` | str | `utm_term` | screen (Geral) |
| `utm_base` | str | `ia` | screen (Geral) |
| `sales_link_regex` | str | `^https?://exemplo\.cc/` | screen (Geral) — D5 |
| `utm_mapping` | JSON obj | `{}` | screen (Mapeamento) — `{ "<user_id>": "<utm_term>" }` |

`config_repo` faz `json.dumps/loads` automático. Reads no filtro com cache TTL curto (30s) para não bater no DB a cada resposta (padrão de `agent/history_filter.py`).

---

## 5. UI de configuração (screen `config:true`)

Como o mapa precisa de **dropdown de usuário** (D2), a config é uma **screen custom** (não o form declarativo — quando existe screen `config:true`, ela **substitui** o form no modal; por isso a screen renderiza também os escalares). `settings.py` fica só declarando os defaults/limites (fonte única de defaults), mas a edição é toda pela screen via `routes.py`.

- **Seção "Geral":** `enabled` (toggle), `lookback_messages` (`<input type=number min=1>`), `utm_param`/`utm_base`/`sales_link_regex` (`.wa-field`). Salva via `PUT /api/plugins/utm_atendente/mapping` (ou um `PUT /config` dedicado).
- **Seção "Mapeamento":** carrega `GET /api/plugins/utm_atendente/users` (o plugin chama `user_repo.list_all()` via `to_thread`, gated `plugin_permission("config")` — **não** depende de `users.manage`); linha = `<select>` de atendente + `.wa-field` do `utm_term` (pré-preenchido `"<base>-"`) + remover. Salva o objeto `{user_id: utm_term}` inteiro.
- **RBAC:** `plugin.yaml` declara `rbac.permissions: [{key: config, ...}]`; `screens[].requires: config`; rotas com `dependencies=[plugin_permission("config")]`. Default-allow em instalação single-password/aberta (não quebra).
- **Dark-mode:** só classes `wa-*` e `.wa-field` (CLAUDE.md "Tema e modo escuro"). Testar com `.dark` ligado.

---

## 6. Fases / Roadmap

### 6.1 Diagrama de dependências

```
WAVE 0   F1 (scaffold)                                   ← sozinha (funda o pacote)
            │  (barreira: tudo depende do plugin.yaml + config_store)
WAVE 1   F2 (utm.py puro) · F3 (selection.py) · F5 (routes+config)   ← 3× 🟢 paralelas
            │  (F4 precisa de F2+F3; F6 precisa de F5)
WAVE 2   F4 (filters wiring)[dep F2,F3] · F6 (screen)[dep F5]        ← 🟢 paralelas entre si
            │
WAVE 3   F7 (testes)[dep F2,F3,F4]                        ← sozinha
            │
WAVE 4   F8 (zip + smoke manual)                          ← sozinha (fecha)
```

### 6.2 Tabela de fases

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F1** Scaffold | manifest+pacote | 🔴 | baixo | plugin aparece em `/plugins` (desativado), sem `load_error` |
| 1 | **F2** `utm.py` puro | rewrite | 🟢 `[bloqueia: F4,F7]` | médio | testes puros de reescrita passam |
| 1 | **F3** `selection.py` | gatilho/DB | 🟢 `[bloqueia: F4,F7]` | médio | seleção retorna o `utm_term` certo dado um `conversation_id` semeado |
| 1 | **F5** `routes.py`+config | backend UI | 🟢 `[bloqueia: F6]` | baixo | `GET /users` e `GET/PUT /mapping` respondem `{ok,data}` |
| 2 | **F4** `filters.py` wiring | integração | 🟢 `[dep: F2,F3]` | médio | ativar plugin → resposta da IA com link de venda recebe UTM do atendente; envio manual **não** |
| 2 | **F6** screen `config:true` | frontend | 🟢 `[dep: F5]` | médio | modal Configurar mostra Geral+Mapeamento, salva e relê; legível no dark |
| 3 | **F7** testes | qualidade | 🔴 `[dep: F2,F3,F4]` | baixo | suíte do plugin verde (puros + integração) |
| 4 | **F8** empacotar | release | 🔴 | baixo | `.zip` importável instala e roda num ambiente limpo |

### 6.3 Blocos de fase

#### Fase 1 — Scaffold do plugin
- **Objetivo:** pacote descoberto e carregável, sem lógica.
- **Itens:** `plugin.yaml` (id `utm_atendente`, `entry: {filters, routes, settings}`, `screens[config:true]`, `rbac.permissions:[config]`, `permissions:[db.read]`); `__init__.py`; `settings.py` com os 5 escalares (§4.4); `config_store.py` com defaults. `[sequencial]`
- **Pronto quando:** copiar a pasta para `storages/plugins/`, reiniciar, o card aparece em `/plugins` como **desativado** e sem `load_error`.

#### Fase 2 — `utm.py` (reescrita pura) `[paralelo]`
- **Objetivo:** porta fiel do `_apply_utm_term` do Nexus, com `param`/`base`/`sales_re` (D5/D6).
- **Itens:** `apply_utm(...)` + `has_sales_link(...)` (§4.3). Sem I/O, sem DB.
- **Pronto quando:** testes puros (§7) cobrindo: substitui `utm_term=ia`; anexa quando ausente; preserva outro `utm_term`; ignora link fora da regex; idempotência; múltiplas URLs numa parte.

#### Fase 3 — `selection.py` (gatilho + DB) `[paralelo]`
- **Objetivo:** dado um phone, achar o `utm_term` do atendente humano mais recente nas últimas N.
- **Itens:** resolução phone→conversa (§2.6); `SELECT` cru das últimas N (§2.5); varredura DESC + match no mapa (§4.2). Cache TTL do mapa.
- **Pronto quando:** com uma conversa semeada (nota privada humana `sent_by_user_id=15` + `utm_mapping={"15":"ia-atendente"}`), retorna `"ia-atendente"`; nota da IA (`sent_by_user_id NULL`) ⇒ `None`; atendente fora do mapa ⇒ `None`.

#### Fase 4 — `filters.py` (wiring) `[dep F2,F3]`
- **Objetivo:** ligar tudo no `filter.reply.parts` com guarda de `source` e fail-open (D3/D4/P1).
- **Itens:** `rewrite_utm(ctx, parts)` (§4.1); `FILTERS = {"filter.reply.parts": (rewrite_utm, 90)}`; guarda `private_ai_note`; fast-path; try/except retornando `parts`.
- **Pronto quando:** ativar o plugin; IA responde com `https://exemplo.cc/v7?utm_term=ia` após nota privada da Atendente → sai `...utm_term=ia-atendente`; envio **manual** do operador com o mesmo link → **inalterado** (D4); sem nota humana → inalterado.

#### Fase 5 — `routes.py` + persistência `[paralelo]`
- **Objetivo:** endpoints da tela.
- **Itens:** `GET /users` (`user_repo.list_all` via `to_thread`); `GET /mapping` (retorna escalares + `utm_mapping`); `PUT /mapping` (valida e grava `plugin.utm_atendente.*`); todos `dependencies=[plugin_permission("config")]`; formato `{ok,data|error}`.
- **Pronto quando:** `curl` nos 3 endpoints responde ok; `PUT` persiste e `GET` relê.

#### Fase 6 — Screen `config:true` `[dep F5]`
- **Objetivo:** UI de Geral + Mapeamento (§5).
- **Itens:** `static/utm_atendente.js` (default export Preact/HTM); consumir `apiBase`; `authHeaders()`; `wa-*`/`.wa-field`; `can("config")`.
- **Pronto quando:** modal Configurar mostra e salva N/param/base/regex/enabled e o mapa (dropdown de usuário); recarregar mantém; **dark-mode legível**.

#### Fase 7 — Testes `[dep F2,F3,F4]`
- **Objetivo:** travar o comportamento.
- **Itens:** ver §7.
- **Pronto quando:** `tests/test_utm_atendente.py` verde no Postgres de teste.

#### Fase 8 — Empacotar `.zip` + smoke `[dep tudo]`
- **Objetivo:** artefato distribuível.
- **Itens:** `GET /api/plugins/utm_atendente/export` (ou zip manual sem `__pycache__`); importar num ambiente limpo; smoke E2E.
- **Pronto quando:** import instala `enabled=0`; ativar + configurar + disparar reescreve corretamente.

---

## 7. Testes

| Nível | Arquivo | Cobre |
|---|---|---|
| Puro (sem DB) | `tests/test_utm_atendente.py::test_apply_utm_*` | substitui/anexa/preserva/ignora-fora-da-regex/idempotência/multi-URL/param+base custom |
| Integração (Postgres) | `tests/test_utm_atendente.py::test_selection_*` | semeia `contacts`+`atendimentos`+`messages` (nota humana `sent_by_user_id`, nota IA sem autor) e valida `select_term_for_phone` (humano recente vence; IA ignorada; mapa vazio ⇒ None) |
| Integração (filtro) | mesmo arquivo | chama `rewrite_utm(ctx_fake, parts)` com `source` None/`private_ai`/`private_ai_note` e confere aplicar/aplicar/pular |

- Referência de teste de plugin já no repo: `tests/test_website_widget.py` (mesmo padrão de semear DB + Postgres de teste). Roda com `WHATSBOT_TEST_DB_URL` (ver [[postgres-test-db-needs-utf8]]).
- ⚠️ Rodar por arquivo (`venv/bin/python -m pytest tests/test_utm_atendente.py -q`) — a coleção inteira quebra por scripts standalone ([[pytest-tests-nao-roda-inteiro]]).

---

## 8. Riscos e cuidados

| # | Ponto | Risco | Mitigação |
|---|---|---|---|
| R1 | Fail-open | Bug no plugin bloqueia envio da IA | Filtro em `try/except` que SEMPRE retorna `parts`; nunca `None`. O core também isola (`events.py:558`). P1. |
| R2 | Idempotência | Reprocessar duplica a UTM | Regex com lookahead + ramo "já tem `utm_param=`" (§4.3). Testado em F2/F7. |
| R3 | Multi-canal | `extras` sem `channel_id` ⇒ conversa resolvida pelo phone pode ser a de outro canal | Best-effort: conversa **aberta mais recente**. Instalações single-channel (GOWA default inbound) não afetadas. Ver P1. |
| R4 | Custo por resposta | `SELECT` a cada resposta pública | Fast-path (só continua se alguma parte tem link que casa a regex) **antes** de tocar o DB; `LIMIT N`; cache TTL do mapa/settings. |
| R5 | Regex do usuário inválida | `re.compile` levanta | Compilar em `try/except` ⇒ regex ruim = feature no-op (fail-open) + log. Validar no `PUT` da tela e avisar. |
| R6 | Single-password | `sent_by_user_id` NULL nas notas ⇒ nada casa | Feature simplesmente não atribui (no-op seguro). Documentar que D2 (por id) pressupõe multiusuário. Ver P2. |
| R7 | Restart de plugin | Ativar/desativar exige supervisor | Padrão do repo (dev: `restart.py`; Docker: `restart: unless-stopped`). Sem ação nova. |
| R8 | Prioridade de filtros | Outro plugin em `filter.reply.parts` (ex. um guard) pode rodar antes/depois | `priority=90` (roda antes do default 100). Se coexistir com um guard que aborta (`None`), a cadeia encerra — comportamento aceitável (nada a enviar). |
| R9 | `term` com caractere de query | `utm_term` digitado com `&`/espaço quebra a URL | Sanitizar/validar o valor no `PUT` (slug `[A-Za-z0-9._-]`); documentar na tela. |

---

## 9. Perguntas em aberto

- **P1 — Expor `channel_id`/`conversation_id` no `extras` do filtro?** ⏸️ **ADIADO.** Tornaria a resolução exata em multi-canal, mas é mudança no core (viola P0). Contexto: R3. Opções: (a) aceitar best-effort por phone **[recomendado agora]**; (b) num plano futuro, 1 linha no core adicionando `channel_id` ao `extras` de `filter.reply.parts` nos 2 call sites. Recomendação: (a) até surgir caso real multi-canal.
- **P2 — Comportamento em single-password (sem `users.id`)?** ✅ **DECIDIDO (2026-07-13):** no-op seguro (R6). A feature exige multiusuário para atribuir; sem ele, não reescreve. Sem fallback por nome (D2). Documentar na tela.
- **P3 — Storage do mapa: JSON em config vs tabela?** ✅ **DECIDIDO (2026-07-13):** JSON em `plugin.utm_atendente.utm_mapping` (§4.4) — mapa pequeno, sem migration. (b) tabela `plugin_utm_atendente_map` fica como evolução se precisar auditoria/consulta.
- **P4 — Valor do mapa: slug (`anna`) ou termo completo (`ia-atendente`)?** ✅ **DECIDIDO (2026-07-13):** **termo completo** (`ia-atendente`), igual ao Nexus (substituição direta). A tela pré-preenche `"<base>-"` ao adicionar, então o usuário só completa o slug. `base` continua sendo apenas o alvo do match.
- **P5 — Precedência com 2+ atendentes humanos nas últimas N?** ✅ **DECIDIDO (2026-07-13):** **nota humana mais recente** que esteja no mapa vence (varredura DESC, §4.2).

---

## 10. Checklist de verificação

- [ ] Plugin aparece em `/plugins`, ativa/desativa e reinicia sem `load_error`.
- [ ] `tests/test_utm_atendente.py` **verde** no Postgres de teste (`WHATSBOT_TEST_DB_URL`).
- [ ] Reescrita pura: `utm_term=ia`→`utm_term=ia-atendente`; anexa quando ausente; preserva outro `utm_term`; ignora link fora da regex; idempotente; múltiplas URLs.
- [ ] E2E: nota privada da Atendente + IA envia `https://exemplo.cc/...?utm_term=ia` → sai `utm_term=ia-atendente` (webhook **e** fluxo "IA lê").
- [ ] **Envio manual do operador com o mesmo link → INALTERADO** (D4).
- [ ] `source=private_ai_note` → não reescreve (não vai ao WhatsApp).
- [ ] Sem nota humana / atendente fora do mapa → resposta inalterada.
- [ ] Erro forçado (regex inválida, DB indisponível) → resposta **enviada intacta** (fail-open), nunca abortada.
- [ ] Screen Configurar: salva/relê N, param, base, regex, enabled e o mapa; **dark-mode legível** (`wa-*`/`.wa-field`).
- [ ] RBAC: rotas e screen gated por `plugin.utm_atendente.config`; default-allow em single-password.
- [ ] `.zip` importado num ambiente limpo instala `enabled=0` e funciona após ativar+configurar.

---

## Apêndice — arquivos-chave

**Plugin (novo, tudo em `storages/plugins/utm_atendente/`):** `plugin.yaml` · `__init__.py` · `settings.py` · `config_store.py` · `utm.py` · `selection.py` · `filters.py` · `routes.py` · `static/utm_atendente.js`

**Core — só LEITURA/referência (não editar):**
- Hook: `app/services/messaging_service.py:345` · `server/routes/contacts.py:1093` (e a exclusão `contacts.py:747`)
- Autor da nota: `server/routes/contacts.py:1274,1282-1285` · `db/tables.py:129-130`
- Leitura: `db/repositories/message_repo.py:150,454-482` · `plugins/context.py:174` · `storages/plugins/vendas_ia/state.py`
- Resolução de conversa: `db/repositories/contact_repo.py:87` · `db/repositories/conversation_repo.py:207,219,259`
- Usuários: `db/repositories/user_repo.py:46` · `server/routes/users.py:41`
- Infra de plugin: `plugins/events.py:496,514,558` · `plugins/context.py:385-401` · `server/routes/plugins.py:284-326` · `db/repositories/config_repo.py:17,42`
- Referências de estrutura: `assets/plugin_examples/protocolos/` (settings + screen config:true + routes + static) · `assets/plugin_examples/telegram/`

**Origem (Nexus, só referência):** `/opt/nexus/gerenciamento-ia/ai/src/services/{utm_replacer.py, response_dispatcher.py, database.py}`

---

#### Status de execução — Fase 1
**Estado:** ✅ Concluída
- **O que foi feito:** Scaffold do pacote `storages/plugins/utm_atendente/`: `plugin.yaml` (id `utm_atendente`, `entry:{filters,routes,settings}`, screen `config:true` `requires:config`, `rbac.permissions:[config]`, `permissions:[db.read]`), `__init__.py` (vazio), `settings.py` (`class Settings` com os 5 escalares — `enabled`/`lookback_messages`/`utm_param`/`utm_base`/`sales_link_regex`, com títulos/limites), `config_store.py` (DEFAULTS espelho + `get_settings()`/`load_mapping()`/`save_settings()`/`save_mapping()`/`compile_sales_regex()`/`invalidate_cache()`, cache TTL 30s, fail-soft).
- **Como foi feito / decisões:** `config_store` é a casca única sobre `config_repo` (que já faz json enc/dec). Cache TTL 30s (padrão `agent/history_filter.py`) porque o filtro roda a cada resposta pública. Validação de `utm_term` (slug `[A-Za-z0-9._-]{1,64}`) já em `save_mapping` (R9). Regex compilada e cacheada com fail-open (`None` = no-op, R5). `save_*` invalidam o cache (a screen salva e relê na hora, mesmo processo).
- **Problemas / pendências:** nenhuma. `load_manifest` recebe o **dir** (não o arquivo); `screens[].requires`, `rbac`, `entry.filters` aceitos pelo parser.
- **Verificação:** `py_compile` OK em todos os `.py`; `plugins.manifest.load_manifest("storages/plugins/utm_atendente")` retorna id/entry/screens/rbac/permissions corretos (api_version 1.0.0 ∈ `>=1.0,<2.0`). Card em `/plugins` sem `load_error` será confirmado no smoke da F8 (precisa dos módulos `filters`/`routes` existirem — criados nas F4/F5).

#### Status de execução — Fase 2
**Estado:** ✅ Concluída
- **O que foi feito:** `utm.py` puro — `apply_utm(text, term, *, param, base, sales_re)` (porta fiel de `_apply_utm_term`, mas com `param`/`base`/`sales_re` de D5/D6) + `has_sales_link(text, sales_re)` (fast-path). Sem I/O/DB.
- **Como foi feito / decisões:** `_URL = https?://[^\s)\]]+` (idêntico ao Nexus). O `base_pat` é compilado por chamada a partir de `param`/`base` (`([?&])<param>=<base>(?=[&\s)\]\n]|$)`) — só roda depois do fast-path do filtro, custo desprezível. Reescreve só URLs que casam `sales_re` (D5); `utm_term=ia`→substitui, outro valor→intacto, ausente→anexa (D6).
- **Problemas / pendências:** **Endurecido pós-revisão adversarial (F8):** o `_URL` ganancioso (porta do Nexus) engolia pontuação de fim de frase (`.,!?;:`) e `#fragment` → atribuição perdida (`…utm_term=ia.` não casava a base) ou URL corrompida (`…/promo.?utm_term=…`). Adicionado `_peel_trailing` (separa a pontuação de prosa, re-anexa fora da URL; `?` só sai se já há um `?` antes) + split de `#fragment` (o parâmetro entra ANTES do `#`). Correção que o Nexus original NÃO tem.
- **Verificação:** teste inline com 10 casos originais + 14 casos de pontuação/fragmento: substitui base; anexa; preserva outro; ignora fora da regex; base no meio/fim; `ial` não casa; multi-URL; **`…utm_term=ia.`/`!`/`,` → `…ia-atendente` com a pontuação fora**; **`/promo.` → `/promo?utm_term=…` (path intacto, sem `/promo.`)**; **`#top` → parâmetro antes do `#`**. **Idempotência OK em todos.** Coberto formalmente na F7.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída
- **O que foi feito:** `selection.py` — `select_term_for_phone(phone, n)`: `load_mapping` (vazio ⇒ None), `contact_repo.get_by_phone` (grupo ⇒ None), `_resolve_conversation` (`get_open_for_contact` → fallback `get_latest_for_contact`), `SELECT role, sent_by_user_id FROM messages WHERE conversation_id=:cid ORDER BY ts DESC, id DESC LIMIT :n` via `make_plugin_db()`+`text()`, varredura DESC devolvendo o `utm_term` da 1ª nota humana (`role='private_note' AND sent_by_user_id IS NOT NULL`) que está no mapa. Fail-soft (`None` em qualquer erro).
- **Como foi feito / decisões:** `SELECT` cru (não `message_repo.get_context*`) porque `_row_to_dict` OMITE `sent_by_user_id` (§2.5/D2). Tie-break `id DESC` além de `ts DESC` (mensagens no mesmo instante). Resolução por phone é channel-blind (best-effort, R3/P1). `is_group` ⇒ None (venda 1:1). N inválido cai no default 5.
- **Problemas / pendências:** **Endurecido pós-revisão adversarial (F8):** o `SELECT` cru NÃO excluía papéis painel-only — os cards `tool_call` que a IA grava DURANTE o turno (um por tool) empurravam a nota humana para fora da janela N (default 5) → atribuição perdida justamente no fluxo de venda com ferramentas (o caso de uso!). Adicionado `AND role NOT IN ('transcription','tool_call','system_notice','conversation_event','system','error')` (a MESMA lista de `get_context_by_conversation`; literais fixos, sem injeção). Confirmado que `ContactMemory.add_message(..., sent_by_user_id=…)` cria/vincula a conversa e grava o autor — mesma via de `contacts.py:1282`.
- **Verificação:** integração no `whatsbot_test_49`: A) humano mapeado ⇒ **ia-atendente**; B) fora do mapa ⇒ **None**; C) nota da IA (NULL) ⇒ **None**; D) mapa vazio ⇒ **None**; E) 2 humanos, o mais recente (Bob=16) vence ⇒ **ia-bob** (P5); F) phone desconhecido ⇒ **None**; **G) 5 cards `tool_call` + nota humana com N=5 ⇒ ainda `ia-atendente`** (a exclusão evita a diluição). Todos ✅.

#### Status de execução — Fase 4
**Estado:** ✅ Concluída
- **O que foi feito:** `filters.py` — `rewrite_utm(ctx, parts)` + `FILTERS = {"filter.reply.parts": (rewrite_utm, 90)}`. Ordem (§4.1): `enabled`→`isinstance(list)`+não-vazio→guarda `source ∈ {private_ai_note}` pula→`compile_sales_regex` (None⇒no-op)→fast-path `has_sales_link`→`phone`→`select_term_for_phone`→`apply_utm` por parte. Envolto em `try/except` que SEMPRE retorna `parts`.
- **Como foi feito / decisões:** hook = `filter.reply.parts` (plural) — cobre IA-webhook (source None) + IA-lê pública (`private_ai`) e exclui envio manual do operador (que só dispara `.part` singular, D4). `private_ai_note` pulado (não vai ao WhatsApp, D3). Prioridade 90 < default 100 (R8). Fail-open absoluto: NUNCA `None`, NUNCA levanta (P1).
- **Problemas / pendências:** nenhuma. (O input `None` degenerado — impossível em prod, onde `parts` é sempre lista não-vazia — passa por sem virar abort; o plano §4.1 prescreve `return parts` nesse guard.)
- **Verificação:** integração no `whatsbot_test_49` com nota humana Atendente (15→ia-atendente): (1) source None ⇒ **ia-atendente**; (2) `private_ai` ⇒ **ia-atendente**; (3) `private_ai_note` ⇒ **inalterado**; (4) sem link de venda ⇒ inalterado; (5) link sem `utm_term` ⇒ **anexa** `?utm_term=ia-atendente`; (6) sem nota humana ⇒ inalterado; (7) `enabled=false` ⇒ inalterado; (8) `select_term_for_phone` forçado a levantar ⇒ **`parts` intacto (não None)** — fail-open; (9) `[]`⇒`[]`. `validate_filters` aceita o dict (`filter.reply.parts`).

#### Status de execução — Fase 5
**Estado:** ✅ Concluída
- **O que foi feito:** `routes.py` — `router = APIRouter()` com `GET /users` (`user_repo.list_all` via `to_thread` → `[{id,name,email,is_active}]`), `GET /mapping` (`{settings:{5 escalares}, mapping:{}}`), `PUT /mapping` (valida + persiste escalares e/ou mapa via `config_store.save_settings`/`save_mapping` em `to_thread`). Todos `dependencies=[plugin_permission("config")]`; envelope `{ok,data|error}`.
- **Como foi feito / decisões:** PUT valida ANTES de gravar (atômico): regex compila (R5→400 com msg), `lookback≥1` inteiro, `utm_param`/`utm_base` não-vazios, cada `utm_term` não-vazio casa `[A-Za-z0-9._-]{1,64}` (R9→400); linha de mapa em branco é ignorada (usuário sem termo). `/users` não depende de `users.manage` (é `plugin_permission("config")`, default-allow em aberto — R6/P2).
- **Problemas / pendências:** `build_test_app` copia de `assets/plugin_examples`; como o plugin mora em `storages/plugins`, o teste aponta `tests.support.REAL_PLUGIN_EXAMPLES` para `storages/plugins` (padrão reutilizado no F7). **Endurecido pós-revisão (F8):** PUT passou a rejeitar `lookback_messages > 100` (paridade com o `le=100` do `Settings`, R4) e `utm_param`/`utm_base` são **trimados** antes de persistir (`config_store._coerce_ident`) — um espaço perdido quebrava o match silenciosamente.
- **Verificação:** via `build_test_app(["gowa","utm_atendente"])` no `whatsbot_test_49`: plugin carrega `enabled=True, load_error=None` (filters=1, screens=1, RBAC=1 — cobre tb. F1/F8); `GET /mapping`⇒defaults+mapa vazio; `GET /users`⇒ok; `PUT /mapping`⇒persiste (lookback 7 + `{15:ia-atendente,16:ia-bob}`) e relê; `PUT` regex inválida⇒**400** "Regex … inválida"; `PUT` term inválido `'ia anna & x'`⇒**400** com dica.

#### Status de execução — Fase 6
**Estado:** ✅ Concluída
- **O que foi feito:** `static/utm_atendente.js` — screen `config:true` (default export Preact/HTM). Seção **Geral** (Toggle `enabled`, `lookback_messages` number 1–100, `utm_param`, `utm_base`, `sales_link_regex` em `.wa-field`) + seção **Mapeamento** (linha = `<select>` de atendente + `.wa-field` do `utm_term` + Remover; "+ Adicionar" pré-preenche `"<base>-"`, P4). Único "Salvar" faz `PUT /mapping` com escalares + `{user_id: utm_term}` e relê a resposta. `apiBase`/`can("config")` consumidos; erro do backend (400) exibido inline.
- **Como foi feito / decisões:** dark-mode só com semânticas `wa-*` (`bg-wa-panel`/`text-wa-text`/`text-wa-secondary`/`border-wa-border`/`bg-wa-teal`/`bg-wa-hover`/`bg-wa-bg`) + `.wa-field` em inputs/selects; knob branco e "Remover" vermelho como acentos (permitido). Dropdown de cada linha esconde ids já usados em OUTRAS linhas (evita atendente duplicado). Aviso quando não há usuários (R6/P2 — exige multiusuário).
- **Problemas / pendências:** legibilidade dark-mode não é testável headless — seguidas as regras do CLAUDE.md "Tema e modo escuro" à risca (só `wa-*`/`.wa-field`). O `\.` de exibição na dica foi escrito `\\.` (cook limpo em tagged template htm).
- **Verificação:** `node --check` OK. Via `build_test_app(["gowa","utm_atendente"])`: manifest expõe a screen (`config:true`, `requires:config`, component `/plugins/utm_atendente/static/utm_atendente.js`); `GET` do component ⇒ **200** com o `export default function UtmAtendenteConfig` (11 KB). Salvar/relê exercitado pelos endpoints na F5.

#### Status de execução — Fase 7
**Estado:** ✅ Concluída
- **O que foi feito:** `tests/test_utm_atendente.py` — **36 testes** em 4 níveis: (1) puro `apply_utm`/`has_sales_link` (12); (2) `selection.select_term_for_phone` integração (8: humano recente/2-humanos/IA-ignorada/fora-do-mapa/mapa-vazio/phone-desconhecido/grupo/janela-lookback); (3) `filters.rewrite_utm` integração+fail-open (10: source None/private_ai/private_ai_note/sem-nota/disabled/sem-link/`select`-raise/regex-inválida/`[]`/sem-phone); (4) smoke via `build_test_app` (6: carga sem load_error/mapping roundtrip/PUT 400 regex/PUT 400 term/`/users`/screen 200).
- **Como foi feito / decisões:** módulos do plugin carregados sob package **próprio** `utm_ut_pkg` (resolve relativos `from . import` e NÃO colide com o `whatsbot_plugins.utm_atendente` do loader real usado nos testes de rota). Seed via `ContactMemory.add_message(..., sent_by_user_id=…)` (via de produção). `_configure(...)` fixa toda a config por teste (order-independent). `_engine_ready` (conftest) reseta o schema 1×/sessão.
- **Problemas / pendências:** nenhuma. Rodado por arquivo (coleção inteira quebra — [[pytest-tests-nao-roda-inteiro]]).
- **Verificação:** `WHATSBOT_TEST_DB_URL=…whatsbot_test_49 venv/bin/python -m pytest tests/test_utm_atendente.py -q` ⇒ **36 passed** (1 warning irrelevante: `StarletteDeprecationWarning` vindo de `tests/support.py`, não do plugin).

#### Status de execução — Fase 8
**Estado:** ✅ Concluída
- **O que foi feito:** (1) Empacotado o `.zip` distribuível (9 arquivos, `plugin.yaml` na raiz, sem `__pycache__`/`.pyc` — mesmo layout do `GET /api/plugins/<id>/export`) em `…/scratchpad/utm_atendente-plugin.zip`. (2) Smoke de import num ambiente limpo. (3) **Revisão adversarial multi-lente** (Workflow, 4 lentes × verificação, 12 agentes) — achou e **corrigi** 4 defeitos reais. (4) Checklist §10.
- **Como foi feito / decisões:** o plugin vive em `storages/plugins/` (gitignored, distribuído por `.zip` — P0/[[plugin-changes-distributed-via-zip]]), então **isolamento confirmado**: `git status` não mostra nada em core; só mudam `docs-planos/49-*.md` e `tests/test_utm_atendente.py`. **Correções da revisão** (todas dentro do plugin): (a) `utm.py` — pontuação de fim de frase/`#fragment` (ver F2); (b) `selection.py` — exclusão de papéis painel-only (ver F3); (c) `config_store.py`/`routes.py` — trim de `utm_param`/`utm_base` + teto de `lookback` (ver F5). `.zip` reconstruído após as correções.
- **Problemas / pendências:** nenhuma. (Faithful-port do Nexus tinha os mesmos bugs de pontuação/`#`; aqui foram corrigidos.) **Ajuste pós-teste em produção (2 rodadas):** (1º) a oferta OFERTAX usa `exemplo.net` (não `exemplo.cc`) → o link saía sem UTM (era o domínio, não o código). (2º, a pedido do usuário) o casamento de links virou uma **LISTA DE DOMÍNIOS em banco**, gerenciável na tela Configurar — `config.plugin.utm_atendente.sales_domains` (default `["exemplo.cc","exemplo.net"]`). O matcher é DERIVADO da lista (`build_domain_regex`: casa domínio + subdomínios, anti-spoof `exemplo.cc.evil.com`/`notexemplo.cc`), então **acrescentar um domínio novo no futuro = adicionar uma linha, sem regex**. A `sales_link_regex` continua como **override avançado opcional** (vazio = usa a lista). `filters.py` usa `effective_sales_regex(cfg)`. **Auto-heal (3ª rodada):** um teste em produção mostrou que instalações que já tinham a regex-default ANTIGA salva no override não pegavam a lista (o override vencia e só casava `exemplo.cc`). `_effective_override` agora zera as regex-default legadas conhecidas (`_LEGACY_SALES_REGEX_DEFAULTS`) na leitura E na gravação → a lista volta a valer sozinha ao atualizar, sem o operador limpar nada (um override REAL do operador é preservado). Testes: normalização de domínio, domínio novo arbitrário, precedência do override, auto-heal do override legado, roundtrip/validação de rota, E2E em app real (domínio adicionado via endpoint + auto-heal reproduzindo o cenário do usuário).
- **Verificação:** **44 passed** (`pytest tests/test_utm_atendente.py`). Import a frio: `POST /api/plugins/import` ⇒ `{ok, enabled:false}` + `load_error:None`. E2E pelo **bus real** `apply_filter("filter.reply.parts", …)`: nota Atendente + **5 cards tool_call de ruído** + link no fim de frase `https://exemplo.cc/promo.` ⇒ `https://exemplo.cc/promo?utm_term=ia-atendente.` (ambas as correções juntas, em app real). D4 estrutural: `utm_atendente` está no bus `filter.reply.parts` e **ausente** de `filter.reply.part` (singular) ⇒ envio manual nunca reescrito.

---

### ✅ Checklist §10 (todos verificados)

- [x] **Plugin aparece em `/plugins`, ativa/desativa sem `load_error`** — carga `enabled=True, load_error=None` (F5); import a frio `enabled=0` (F8).
- [x] **`tests/test_utm_atendente.py` verde** — 44 passed no `whatsbot_test_49`.
- [x] **Reescrita pura** (substitui/anexa/preserva/ignora-fora-da-regex/idempotente/multi-URL + robusta a pontuação e `#fragment`) — F2/F7.
- [x] **E2E: nota Atendente → link sai `utm_term=ia-atendente`** (webhook `source=None` **e** IA-lê `private_ai`) — F4/F7 + E2E bus F8.
- [x] **Envio manual do operador → INALTERADO (D4)** — `utm_atendente` só no bus `.parts`, ausente do `.part` singular (F8).
- [x] **`source=private_ai_note` → não reescreve** — F7 `test_filter_skips_private_ai_note`.
- [x] **Sem nota humana / atendente fora do mapa → inalterado** — F7 selection + filter.
- [x] **Erro forçado (regex inválida, `select` levanta) → enviada intacta (fail-open, nunca None)** — F7 `test_filter_failopen_*`.
- [x] **Screen Configurar: salva/relê N/param/base/regex/enabled + mapa; dark-mode `wa-*`/`.wa-field`** — F6 served 200 + F7 roundtrip.
- [x] **RBAC: rotas + screen gated por `plugin.utm_atendente.config`; default-allow single-password** — `dependencies=[plugin_permission("config")]` + `requires:config`; testes passam sem auth (default-allow).
- [x] **`.zip` num ambiente limpo instala `enabled=0` e funciona após ativar** — F8 import + E2E.
