# Plano 94 — Tela "Jornada do cliente": plugin `trackify` no cabeçalho da conversa

> **Status:** PLANEJAMENTO · **Data:** 2026-07-30 · **Escopo:** médio/grande
> **Origem:** pedido do usuário ("implementar tela com histórico do cliente mostrando tudo que ele fez pelo Trackify"). **Método:** leitura do código do Trackify em `/opt/nexus/trackify` (NestJS + Prisma + Next), leitura do seam de plugin do WhatsBot, e **consultas medidas no banco de produção** via MCP do cofre (credencial `banco-privado-*`, somente leitura) — os números deste plano são medidos, não estimados.
> Um botão novo no cabeçalho da conversa abre um modal com a **jornada do contato no Trackify** (CDP do Nexus): quem ele é, quanto gastou, o que comprou, o que falhou, o que cancelou e quanto falta da assinatura. É um **plugin novo** (`trackify`), leitura pura, sem tocar no core.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| D1 ✅ | O acesso é por um **botão a mais na barra de cima** da conversa (onde aparece nome/número) | Slot `conversation.header.actions` + modal — precedente exato: `agendamento_retorno` |
| D2 ✅ | A tela integra com o **Trackify** (módulo do Nexus) | Nada é gravado no Trackify: **read-only** |
| D3 ✅ | Deve mostrar a **jornada completa**: entrou na página, comprou, cancelou, tempo restante de curso | Timeline de `events` + painel derivado de assinatura. ⚠️ "entrou na página de vendas" **não existe** como evento hoje — ver P1 |
| D4 ✅ | Objetivo é o **vendedor conferir o cliente rápido** durante o atendimento | Modal (não uma aba nova), abre já no contexto do contato da conversa |
| D5 ✅ | É um **plugin novo e autocontido** (`trackify`), NÃO uma adição ao core nem a um plugin existente | Toda a responsabilidade (leitura do CDP, tela, settings, RBAC) vive em `storages/plugins/trackify/`. Alterar a jornada = mexer só nesse plugin. Nenhum arquivo do core e nenhum outro plugin é tocado — nem o `protocolos`, nem o `vendas_ia` (dele só se **copia** um arquivo, não se importa) |
| D6 ✅ | A **escrita** de volta no Trackify (espelhar e-mail/CPF que o atendente preencher, compra fechada, etc.) fica para uma **v2**, mas a estrutura da v1 já tem que caber nela | §10 documenta o caminho de escrita **já verificado no código do Trackify** e como o layout de módulos da v1 o acomoda sem refactor |

---

## 1. Resumo executivo

O Trackify é o CDP do Nexus: `contacts` + `events` + campos dinâmicos (`custom_fields`/`contact_field_values`, `event_custom_fields`/`event_field_values`) + `channels`, com **11.359 contatos** e **14.668 eventos** em produção. Um contato é identificado por **campos marcados como identificador**: `email` (prioridade 10), `whatsapp` (20), `cpf` (30), `telegram_id` (40).

O WhatsBot não tem o `contact_id` do Trackify — a ponte prática é o **telefone**. Medido em amostra de 500 contatos reais do WhatsBot de produção contra o Trackify de produção:

| Estratégia de casamento | Acertos em 500 |
|---|---|
| Comparação **literal** (`contacts.phone` == `contact_field_values.value`) | **30 (6%)** |
| Comparação por **variantes canônicas BR** (com/sem `+`, com/sem o 9º dígito) | **115 (23%)** |

Ou seja: **sem a canonicalização, a tela nasce quebrada** (94% de "não encontrado"). A causa é que o WhatsBot guarda majoritariamente 12 dígitos (JID do WhatsApp, sem o 9) e o Trackify majoritariamente 13 com `+`.

A entrega é um plugin novo `trackify`: engine read-only para o banco do Nexus (precedente `vendas_ia`), 3 endpoints REST, e uma tela em modal aberta por um botão no cabeçalho da conversa.

---

## 2. Como funciona hoje (mapa)

### 2.1 Trackify — onde vive e o que tem

| Item | Fato medido |
|---|---|
| Código | `/opt/nexus/trackify` — NestJS 11 + Fastify + Prisma 6 + Next 15, porta padrão 8012 (`server/main.ts`) |
| Banco | As tabelas do Trackify vivem no **mesmo banco do Nexus** (`public`), não num banco próprio — confirmado por `information_schema.tables` |
| Schema | `/opt/nexus/trackify/prisma/schema.prisma` — `Contact`, `Event`, `CustomField`, `ContactFieldValue`, `EventCustomField`, `EventFieldValue`, `Channel`, `Tag`, `ContactTag`, `ContactChangelog` |
| Autenticação da API | `AuthGuard` de `@nexus/core/server`, sessão via cookie `trackify_session` (SSO do Nexus). **Não há token de serviço** aparente — ver §3.1 |
| Contatos | 11.359 (10.268 `customer`, resto `lead`/`inactive`) |
| Eventos | 14.668 |
| Contatos com WhatsApp | 10.907 |
| Duplicidade por número | 10.897 números → 1 contato; **5 números → 2 contatos** (residual, tratável) |

**Identificadores** (`custom_fields WHERE is_identifier`):

| slug | Nome | Prioridade |
|---|---|---|
| `email` | Email | 10 |
| `whatsapp` | WhatsApp | 20 |
| `cpf` | CPF | 30 |
| `telegram_id` | Telegram ID | 40 |

Campos não-identificadores: `name`, `address`, `street`, `city`, `state`, `country`, `zipcode`.

**Canais e tipos de evento em produção** (top, `deleted_at IS NULL`):

| Canal | Tipo de evento | Total | Janela |
|---|---|---|---|
| `ticto` | `purchase` | 9.563 | mar–abr/2026 |
| `disparo-api-whatsapp` | `disparo_whatsapp` | 1.424 | jun–jul/2026 |
| `ticto` | `active_subscription` | 1.101 | abr/2026 |
| `ticto` | `authorized` | 814 | 2023 → **hoje** |
| `ticto` | `pix_created` | 783 | 2025 → **hoje** |
| `ticto` | `refused` | 313 | 2023 → hoje |
| `disparo-api-whatsapp` | `importacao_lista` | 135 | jul/2026 |
| `ticto` | `subscription_canceled` | 109 | 2024 → abr/2026 |
| `checkout-pagarme` | `initiate_checkout`, `lead_email`, `lead_document`, `lead_whatsapp`, `lead_nome`, `payment_method`, `pix_generated`, `coupon_applied` | 49 / 42 / 40 / 39 / 4 / 40 / 6 / 5 | jun/2026 |
| `pagarme` | `charge.paid`, `order.paid`, `order.payment_failed`, `charge.payment_failed`, `charge.antifraud_reproved`, `charge.refunded`, `order.canceled` | 30 / 29 / 27 / 17 / 10 / 4 / 4 | jun/2026 |
| `ticto` | `bank_slip_created`, `pix_expired`, `subscription_delayed`, `refunded`, `card_exchanged`, `chargeback`, `claimed`, `waiting_payment`, `bank_slip_delayed` | 20 / 18 / 33 / 3 / 2 / 1 / 1 / 1 | — |

**Campos dinâmicos de evento** (25 definidos, uso medido): `offer_name` (12.489), `status` (12.391), `offer_id`, `transaction_id`, `installments`, `payment_method`, `product_name` (12.264), `utm_campaign/medium/content/source/term`, `product_id`, `card_brand`, `subscription_interval` (1.607), `is_subscription`, `fee`, `commission`, `failed_charges`, `successful_charges`, **`next_charge_date` (1.472)**, `subscription_canceled_at` (1.212), `card_last_digits`, `subscription_id` (482), `affiliate_name` (0).

### 2.2 O seam do WhatsBot (o que já existe e será reusado)

| Item | Onde | Papel |
|---|---|---|
| Slot do cabeçalho | [ConversationHeaderActions.js:225](../web/static/js/components/contacts/ConversationHeaderActions.js#L225) `<Slot name="conversation.header.actions" ctx={{conv, user}} />` | Onde o botão entra |
| Precedente de botão + modal | [agendamento_retorno/static/extends.js:22-60](../storages/plugins/agendamento_retorno/static/extends.js#L22-L60) | Template literal do que fazer (`api.addSlot` + `api.ui.openModal`) |
| Precedente de banco externo read-only | [vendas_ia/nexus_db.py:71-138](../storages/plugins/vendas_ia/nexus_db.py#L71-L138) | `create_engine` com `_normalize_dsn` (força `+psycopg`), `sslmode=require`, `pool_pre_ping`, `run_read()` devolvendo `list[dict]`; DSN vem de setting, **nunca** de código |
| Transporte HTTP do plugin | [plugins/api.js:121](../web/static/js/plugins/api.js#L121) `buildPluginHttp` | `api.http.get('/rota')` resolve para `/api/plugins/trackify/rota` |
| RBAC de plugin | [plugins/context.py](../plugins/context.py) `plugin_permission("view")` | Gate das rotas |
| Auditoria de plugin | [plugins/context.py](../plugins/context.py) `audit(...)` | **Não** auditar leitura (regra: GET/listagem fica fora) |

⚠️ **Gotcha de DSN** ([vendas_ia/nexus_db.py:72-82](../storages/plugins/vendas_ia/nexus_db.py#L72-L82)): um DSN colado como `postgresql://` faz o SQLAlchemy escolher `psycopg2`, que **não está instalado** — a engine vira `None` e tudo some em silêncio. O normalizador reescreve para `postgresql+psycopg://`. Copiar essa função é obrigatório (ver memória "vendas_ia search config gotchas").

---

## 3. A ponte de identidade (a parte que decide se a tela presta)

### 3.1 Banco direto × API REST — decisão

| Opção | Prós | Contras | Veredito |
|---|---|---|---|
| (a) **Ler o banco do Nexus direto** (SQLAlchemy read-only) | Precedente pronto (`vendas_ia`); sem dependência de sessão SSO; 1 roundtrip; índices existentes servem | Acoplado ao schema do Trackify (mudança de schema quebra) | ✅ **ESCOLHIDA** |
| (b) Chamar a REST do Trackify (`GET /contacts?search=`, `GET /contacts/:id/events`) | Desacoplado do schema | Auth é **cookie de sessão SSO do Nexus** (`AuthGuard` de `@nexus/core/server`) — não há token de serviço; `search` é `contains` sobre TODOS os `contact_field_values`, então buscar por telefone traria falso-positivo de CPF/endereço; sem canonicalização BR do lado de lá | ❌ |

**Consequência:** o plugin tem um setting `nexus_dsn` (mascarado, `format: password`), igual ao `vendas_ia`. **Nenhuma credencial, host ou IP entra em código, em plano ou em URL de query** — travado pelo plano 78 (limpeza de vazamentos).

### 3.2 Casamento por telefone — o problema medido

Formato do telefone nas duas pontas (produção):

| Tamanho (dígitos) | WhatsBot `contacts.phone` | Trackify `whatsapp` |
|---|---|---|
| 12 | **9.583** | 191 |
| 13 | 4.095 | **10.704** |
| outros | 1.076 | 12 |

O Trackify grava com `+` na maioria (`+5513991198852`); o WhatsBot grava só dígitos (`5513991198852` ou `551399119885`… conforme o JID). Daí os 6% de acerto literal.

### 3.3 A solução: variantes literais (índice-friendly)

Existe o índice `idx_cfv_identifier_lookup ON contact_field_values (custom_field_id, value)`. Uma consulta com `regexp_replace()` nos dois lados **não usa o índice** (seria seq scan em 11k+ linhas por abertura de modal). A solução é gerar as **variantes em Python** e usar `value = ANY(:cands)`:

```
p = só os dígitos de contacts.phone
candidatos = [p, '+'+p]
  + (se len(p)==12: [p[:4]+'9'+p[4:], '+'+p[:4]+'9'+p[4:]])   # insere o 9º dígito
  + (se len(p)==13: [p[:4]+p[5:],     '+'+p[:4]+p[5:]])       # remove o 9º dígito
```

**Validado por medição:** a busca por variantes acerta **exatamente os mesmos 115/500** que a canonicalização completa por regex — mesmo recall, com índice.

```sql
SELECT c.id, c.status, c.total_spent, c.first_seen_at, c.converted_at
FROM contact_field_values cfv
JOIN custom_fields cf ON cf.id = cfv.custom_field_id
JOIN contacts c       ON c.id = cfv.contact_id AND c.deleted_at IS NULL
WHERE cf.slug = 'whatsapp' AND cfv.value = ANY(:cands)
```

**Fallbacks encadeados** (a mesma consulta trocando o slug): `whatsapp` → `email` (o WhatsBot tem `contacts.email`, mas só **344 de 14.791** preenchidos = 2%) → `cpf` (se algum dia virar atributo personalizado do contato). Múltiplos acertos (os 5 números duplicados) ⇒ mostrar um seletor "2 cadastros encontrados" em vez de escolher em silêncio.

---

## 4. O que a tela mostra

Modal em 3 blocos, alimentado por 1 chamada (`GET /api/plugins/trackify/journey?phone=…`).

**Bloco 1 — Identidade** (de `contacts` + `contact_field_values`)
`name` · `status` (lead/customer/inactive) · **Total gasto** (`total_spent`) · Primeiro contato (`first_seen_at`) · Convertido em (`converted_at`) · email/whatsapp/cpf/telegram · cidade/estado · tags · botão **"Abrir no Trackify"** (deep-link `<base_url_do_nexus>/trackify/contacts/<uuid>`, com a base vinda de setting — nunca hardcoded).

**Bloco 2 — Assinaturas / "tempo restante"** (derivado dos eventos, ver §4.1)
Por `subscription_id` (ou `product_name` quando ausente): produto, oferta, status, **próxima cobrança** e **dias restantes**, cobranças com sucesso × falhas, cancelada em.

**Bloco 3 — Linha do tempo** (de `events` + `event_field_values`, `occurred_at DESC`, paginada)
Uma linha por evento: data/hora · chip do `event_type` · canal · título · valor (R$) · campos dinâmicos relevantes (produto/oferta/pagamento/UTM) num "expandir". Filtros por canal e por tipo (o vendedor quer ver só compras, ou só falhas).

### 4.1 ⚠️ Armadilhas de dados MEDIDAS (não descobrir em produção)

| Achado | Evidência | Tratamento |
|---|---|---|
| `next_charge_date` é **string `dd/mm/yyyy`**, não `date` | Valores reais: `"25/02/2027"`, `"04/01/2027"` | Parse tolerante; falha de parse ⇒ mostra o texto cru, nunca quebra a tela |
| `subscription_canceled_at` contém **`"system"`** em linhas de `active_subscription` | Valor real observado em 4 de 4 amostras | **Não** tratar como data; só exibir quando casar um formato de data |
| Nem todo evento tem campos dinâmicos | `disparo_whatsapp` volta `campos = NULL` | `jsonb_object_agg(...) FILTER (WHERE slug IS NOT NULL)` — sem o FILTER, o Postgres levanta `field name must not be null` (erro reproduzido durante a investigação) |
| `value` do evento é `Decimal` nullable | `order.paid` tem `value=null` e `charge.paid` tem `1.00` | Formatação PT-BR com fallback "—" |
| 2.557 números com prefixo `5555…` | Medido | **Não** "corrigir": DDD 55 (RS) é legítimo. Não inventar heurística de 55 duplicado — só as variantes do §3.3 |
| ~23% de acerto | Medido em 500 | O estado "sem cadastro no Trackify" é **normal e frequente** — precisa de um vazio bem resolvido, não de um erro |

---

## 5. Inventário do plugin

O plugin é **autocontido** (D5): nada fora desta pasta muda. Do `vendas_ia` se **copia** `nexus_db.py` (arquivo, não import) — plugins nunca dependem uns dos outros.

```
storages/plugins/trackify/
├── plugin.yaml            # id: trackify · rbac: view · frontend_extends
├── __init__.py
├── settings.py            # nexus_dsn (password), nexus_base_url, cache_ttl, timeline_page_size
├── _config.py             # leitura das settings com defaults (padrão vendas_ia)
├── trackify_db.py         # LEITURA: engine read-only + run_read (cópia adaptada de vendas_ia/nexus_db.py)
├── phone.py               # variantes BR — PURO, testável isolado
├── identity.py            # resolve telefone/email/cpf → trackify contact_id  (usado por journey.py HOJE e por ingest.py na v2)
├── journey.py             # identidade + assinaturas + timeline (consome identity.py)
├── routes.py              # 3 endpoints
└── static/
    ├── extends.js         # botão no slot conversation.header.actions
    └── JourneyModal.js    # o modal (Preact + htm)

# v2 (§10) — NÃO implementar agora, mas o layout acima já reserva o lugar:
#   ingest.py              # ESCRITA: POST /ingestion/<slug> (HTTP, nunca banco)
#   events.py              # contact.updated / contact.tagged → ingest.py
#   filters.py             # (se precisar de opt-out por contato)
```

⚠️ **`identity.py` separado de `journey.py` desde a v1 é de propósito**: a v2 (escrita) precisa exatamente da mesma resolução de identidade, e o Trackify resolve o contato pelos identificadores enviados no payload. Fundir os dois na v1 obrigaria a um refactor depois.

| # | Item | Risco | Esforço | Nota |
|---|---|---|---|---|
| I1 | `phone.py` — variantes | Baixo | S | Puro; teste `node`-free, `pytest` simples |
| I2 | `trackify_db.py` | Baixo | S | Copiar `_normalize_dsn` **inteiro** (gotcha psycopg2) |
| I3 | `journey.py` — 3 consultas | Médio | M | Identidade / assinaturas / timeline paginada |
| I4 | `routes.py` | Baixo | S | `GET /journey`, `GET /journey/events`, `GET /health` |
| I5 | `settings.py` + `_config.py` | Baixo | S | `nexus_dsn` mascarado |
| I6 | `static/extends.js` | Baixo | S | Espelho de `agendamento_retorno` |
| I7 | `static/JourneyModal.js` | Médio | L | 3 blocos + filtros + estados vazio/erro + **modo escuro** |
| I8 | `plugin.yaml` + RBAC + zip | Baixo | S | `rbac.permissions: [view]`; zip no repo de plugins do Pro |
| I9 | Testes | Baixo | M | `tests/test_trackify_journey.py` (mock do `run_read`) |

### 5.1 Falsos positivos descartados

| "Problema" aparente | Por que NÃO é |
|---|---|
| "Precisa de migration / tabela no WhatsBot" | Não — leitura pura; nada é persistido |
| "Precisa mexer no core para o botão" | Não — o slot `conversation.header.actions` já existe e é aditivo |
| "Precisa de token de API do Trackify" | Não — decisão (a) do §3.1 é banco direto; a REST exige sessão SSO |
| "Precisa mudar o Trackify" | Não para esta tela. **Só** para P1 (evento de visita à página de vendas), que é ingestão no Trackify, fora deste plano |
| "Dá para casar por `contact_id`" | Não existe correspondência de ids; a ponte é telefone (§3) |
| "Buscar por `GET /contacts?search=<telefone>` resolve" | Não — `search` é `contains` sobre TODOS os field values (`contacts.service.ts` `findAll`): um telefone pode casar pedaço de CPF/CEP |

---

## 6. Fases

```
WAVE 0   F1(phone.py) · F2(trackify_db+settings) · F5(esqueleto do plugin)    🟢 juntas
              │ (barreira: F3 depende de F1+F2)
WAVE 1   F3(journey.py + routes.py)                                           🔴 sozinha
              │
WAVE 2   F4(extends.js + JourneyModal.js) · F6(testes)                        🟢 juntas
              │
WAVE 3   F7(validação contra produção read-only) → F8(zip + import)           🔴 sequencial
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | F1 | `phone.py` — variantes BR | 🟢 | Baixo | `pytest` cobre 12↔13, com/sem `+`, lixo, não-BR |
| 0 | F2 | `trackify_db.py` + `settings.py` + `_config.py` | 🟢 | Baixo | `GET /health` responde `{configured, reachable}` |
| 0 | F5 | Esqueleto (`plugin.yaml`, `__init__.py`, RBAC) | 🟢 | Baixo | Plugin aparece em `/plugins` e ativa sem erro |
| 1 | F3 | `journey.py` + `routes.py` | 🔴 [depende de: F1, F2] | Médio | `GET /journey?phone=` devolve o JSON dos 3 blocos |
| 2 | F4 | `extends.js` + `JourneyModal.js` | 🟢 [depende de: F3] | Médio | Botão aparece; modal renderiza nos 2 temas |
| 2 | F6 | `tests/test_trackify_journey.py` | 🟢 [depende de: F3] | Baixo | Verde com `run_read` mockado |
| 3 | F7 | Validação read-only contra produção | 🔴 | Médio | 10 contatos reais conferidos contra a tela do Trackify |
| 3 | F8 | `.zip` no repo de plugins do Pro + `Importar (.zip)` | 🔴 | Baixo | Vendedor usa em produção |

### F3 — as três consultas (esqueleto verificado)

```sql
-- (1) identidade + campos do contato
SELECT c.id, c.status, c.total_spent, c.first_seen_at, c.converted_at,
       cf.slug, cfv.value
FROM contacts c
LEFT JOIN contact_field_values cfv ON cfv.contact_id = c.id
LEFT JOIN custom_fields cf ON cf.id = cfv.custom_field_id AND cf.deleted_at IS NULL
WHERE c.id = :contact_id AND c.deleted_at IS NULL;

-- (2) timeline paginada (usa idx_events_contact_time)
SELECT e.id, e.event_type, e.title, e.description, e.value, e.occurred_at,
       ch.slug AS channel,
       jsonb_object_agg(ecf.slug, efv.value) FILTER (WHERE ecf.slug IS NOT NULL) AS fields
FROM events e
JOIN channels ch ON ch.id = e.channel_id
LEFT JOIN event_field_values efv ON efv.event_id = e.id
LEFT JOIN event_custom_fields ecf ON ecf.id = efv.event_custom_field_id
WHERE e.contact_id = :contact_id AND e.deleted_at IS NULL
GROUP BY e.id, ch.slug
ORDER BY e.occurred_at DESC
LIMIT :limit OFFSET :offset;

-- (3) tags
SELECT t.name, t.color FROM contact_tags ct JOIN tags t ON t.id = ct.tag_id
WHERE ct.contact_id = :contact_id;
```

O bloco de assinaturas é **derivado em Python** da consulta (2), filtrando `event_type IN ('active_subscription','subscription_canceled','subscription_delayed','purchase')` e agrupando por `subscription_id`/`product_name` — sem consulta extra.

---

## 7. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| **Recall de 23%** | Vendedor abre e vê "não encontrado" na maioria | Vazio explícito e útil ("Nenhum cadastro no Trackify para este número") + campo de busca manual por email/CPF dentro do modal + **não** prometer cobertura total |
| DSN em código/plano/URL | Vazamento (plano 78) | DSN só em setting mascarada; `nexus_base_url` idem; nunca logado, nunca em query string |
| `psycopg2` ausente | Engine `None` e tela vazia em silêncio | `_normalize_dsn` copiado + `GET /health` que distingue "não configurado" de "inalcançável" |
| Banco de PRODUÇÃO do Nexus | Uma consulta ruim afeta o Nexus | Transação **read-only**, `LIMIT` obrigatório, `statement_timeout` curto (~5s) no `connect_args`, cache TTL curto por contato |
| Latência ao abrir o modal | Trava a UI | Consulta indexada (§3.3 + `idx_events_contact_time`) + `asyncio.to_thread` + cache TTL (setting, default 60s) |
| Schema do Trackify muda | Tela quebra em silêncio | `GET /health` valida a presença das colunas usadas; erro vira mensagem acionável, não 500 |
| `jsonb_object_agg` com chave nula | `field name must not be null` (**reproduzido**) | `FILTER (WHERE ecf.slug IS NOT NULL)` |
| Modo escuro | Tela ilegível | Só classes `wa-*` / `.wa-field`; testar com o tema escuro ligado (regra do CLAUDE.md) |
| Vazamento de dado financeiro | Qualquer atendente vê quanto o cliente gastou | RBAC `plugin.trackify.view`; `screens[].requires` esconde de quem não tem |
| 5 números com 2 cadastros | Escolha silenciosa e errada | Seletor "2 cadastros encontrados" |
| Deploy | Produção só recebe por `.zip` | Zip no repo de plugins do Pro + `Importar (.zip)` (memória "Plugin changes via zip") |

---

## 8. Perguntas em aberto

**P1 — "Entrou na página de vendas" não existe no Trackify hoje.**
Medido: os tipos de evento em produção cobrem checkout (`initiate_checkout`, `lead_*`), pagamento (`purchase`, `authorized`, `refused`, `charge.*`, `order.*`), assinatura (`active_subscription`, `subscription_canceled`) e disparo (`disparo_whatsapp`). **Não há pageview.**
(a) A tela mostra só o que existe (`initiate_checkout` é o ponto mais "de topo") — escopo deste plano.
(b) Criar a ingestão de pageview no Trackify (pixel/canal novo) — **outro plano, no repositório do Nexus**.
**Recomendação: (a) agora, (b) depois.** ⏸️ AGUARDANDO DECISÃO.

**P2 — "Tempo restante do curso" vem de onde?**
Não há campo "expira em". O mais próximo é `next_charge_date` (1.472 usos, string `dd/mm/yyyy`) + `subscription_interval`.
(a) Exibir "Próxima cobrança: dd/mm/aaaa (faltam N dias)" — honesto com o dado existente.
(b) Inventar uma data de expiração — ❌ arriscado (vendedor promete prazo errado ao cliente).
**Recomendação: (a).** ⏸️ AGUARDANDO DECISÃO.

**P3 — Busca manual dentro do modal quando o telefone não casa?**
Dado o recall de 23%, um campo "buscar por e-mail/CPF" salva a maioria dos casos.
**Recomendação: sim, na F4** (2 campos + a mesma consulta trocando o slug). ⏸️ AGUARDANDO DECISÃO.

**P4 — Gravar o vínculo encontrado?**
Guardar `trackify_contact_id` num atributo personalizado do contato do WhatsBot tornaria as próximas aberturas instantâneas e exatas.
(a) Não gravar (100% stateless, sempre re-resolve).
(b) Gravar no 1º acerto (cache permanente, some se o Trackify fundir contatos).
**Recomendação: (a) na v1**, reavaliar se a latência incomodar. ⏸️ AGUARDANDO DECISÃO.

**P5 — O botão deve aparecer para todo contato ou só quando há cadastro?**
Saber se há cadastro exige a consulta — ou seja, sondar toda conversa aberta.
**Recomendação: botão sempre visível** (custo zero) e a consulta só no clique. ⏸️ AGUARDANDO DECISÃO.

**P6 — Correção dos 191 telefones de 12 dígitos e dos formatos sujos no Trackify?**
Fora do escopo (é higienização de dados no CDP, com risco de fundir contatos distintos). As variantes do §3.3 já absorvem o caso.
**Recomendação: não fazer aqui.** ⏸️

---

## 10. v2 — espelhar/inserir no Trackify (FORA DO ESCOPO desta entrega)

> Documentado aqui **só** para que a v1 não feche portas. Nada desta seção é implementado no plano 94.

### 10.1 O caminho de escrita já existe e é público (verificado)

O Trackify tem um endpoint de ingestão **por canal**, marcado `@Public()`:

`POST /ingestion/:channelSlug` → [ingestion.controller.ts](/opt/nexus/trackify/server/modules/ingestion/ingestion.controller.ts) (HTTP 202)

O pipeline ([pipeline.ts](/opt/nexus/trackify/server/modules/ingestion/pipeline.ts)) faz, nesta ordem: acha o canal pelo `slug` → **valida a auth do canal** → carrega os identificadores por prioridade → roda o **adapter** → resolve/cria o contato → grava evento + field values + changelog.

**Autenticação por canal** ([auth-validator.ts](/opt/nexus/trackify/server/modules/ingestion/auth-validator.ts)), configurada em `channels.config.auth`:

| `type` | Como | Adequação para o WhatsBot |
|---|---|---|
| `none` | sem auth | ❌ endpoint público sem proteção |
| `api_key_header` | header configurável == `apiKey` | ✅ **recomendado** — simples, chave em setting mascarada |
| `api_key_query` | query param == `apiKey` | ❌ chave em URL (fica em log de proxy) |
| `signature` | HMAC do **raw body**, header configurável, `timingSafeEqual` | ✅ alternativa mais forte |

**O mapeamento payload → campos vive no Trackify, não no WhatsBot** ([adapter.ts](/opt/nexus/trackify/server/modules/ingestion/adapter.ts)): cada linha de `channel_mappings` tem uma expressão **JSONata** (`sourceExpression`), um `targetEntity` (`contact`|`event`) e um `targetField`. O adapter avalia a JSONata contra o payload e roteia o resultado para identificador, campo de contato, campo nativo do evento (`event_type`, `title`, `value`, `external_id`, `occurred_at`) ou `event_field_values`.

**Consequência arquitetural (a parte importante):** o plugin do WhatsBot manda um JSON **do seu próprio formato** e a tradução é configurada na tela do Trackify. Um campo novo no futuro = uma linha de mapping no Trackify, **sem release do plugin**. É a mesma filosofia "o core não conhece o provider" que o WhatsBot já usa.

### 10.2 A regra de ouro: leitura por banco, escrita por HTTP

| Direção | Transporte | Por quê |
|---|---|---|
| **Ler** | Banco read-only (§3.1) | A REST de leitura exige cookie de sessão SSO do Nexus; não há token de serviço |
| **Escrever** | `POST /ingestion/<slug>` | O endpoint é público + autenticado por canal, e **é onde mora a lógica de dedupe/merge/changelog/`total_spent`**. Escrever no banco direto pularia tudo isso e corromperia o CDP em silêncio |

⚠️ **Nunca `INSERT` no banco do Nexus a partir do WhatsBot.** A engine da v1 é `readOnly` de propósito; a v2 não a reaproveita para escrita.

### 10.3 Canais a criar no Trackify (a intuição do usuário, confirmada)

Sim: cada fonte de escrita vira **um canal** no Trackify, com seus mappings. Sugestão de recorte:

| Canal (slug) | Dispara em | Payload leva | Vira, no Trackify |
|---|---|---|---|
| `whatsbot-contato` | `contact.updated` (o atendente preencheu e-mail/CPF/nome no painel) | telefone + os campos preenchidos | Identificadores/campos do contato (sem evento, ou com um evento leve `contact_updated`) |
| `whatsbot-atendimento` | `conversation.created` / `conversation.status_changed` / `conversation.assigned` | telefone + status + atendente + protocolo | Eventos de relacionamento na timeline |
| `whatsbot-etiqueta` | `contact.tagged` / `contact.untagged` | telefone + tag | Tags ou eventos |

Todos os gatilhos **já existem no barramento** do WhatsBot (`contact.updated`, `contact.tagged`, `contact.untagged`, `conversation.*` — ver CLAUDE.md §Events). A v2 é, de novo, **só um `events.py` no plugin**: zero core.

### 10.4 O que a v2 vai precisar decidir (não decidir agora)

| # | Questão | Nota |
|---|---|---|
| V1 | Escrita síncrona no handler ou fila com retry? | O endpoint responde 202; uma falha de rede não pode perder o dado nem travar o painel. Provável: tabela `plugin_trackify_outbox` + reprocesso |
| V2 | Idempotência | O pipeline dedupe por `(channel_id, external_id)` (índice único `idx_events_external`). O plugin precisa gerar um `external_id` estável (ex.: `wb:<conversation_id>:<evento>:<ts>`) |
| V3 | Loop de espelhamento | Se um dia o Trackify escrever de volta no WhatsBot, ida-e-volta vira eco. Marcar a origem no payload |
| V4 | Quais campos o atendente pode espelhar | E-mail e CPF são identificadores no Trackify: um valor errado **funde contatos**. Provável: validar formato antes de enviar |
| V5 | LGPD / consentimento | Mandar dado pessoal do painel para o CDP é decisão de produto, não técnica |

---

## 11. Apêndice — arquivos-chave

**Plugin novo (tudo aqui):**
- `storages/plugins/trackify/{plugin.yaml,__init__.py,settings.py,_config.py,trackify_db.py,phone.py,journey.py,routes.py}`
- `storages/plugins/trackify/static/{extends.js,JourneyModal.js}`

**Precedentes a copiar (leitura):**
- `storages/plugins/vendas_ia/nexus_db.py:71-138` — engine read-only + `_normalize_dsn` + `run_read`
- `storages/plugins/vendas_ia/settings.py:21-35` — setting de DSN mascarada
- `storages/plugins/agendamento_retorno/static/extends.js:22-60` — botão no slot + `api.ui.openModal`

**Core (só leitura — NÃO muda):**
- `web/static/js/components/contacts/ConversationHeaderActions.js:225` — o slot
- `web/static/js/plugins/api.js:121` — `buildPluginHttp`
- `plugins/context.py` — `plugin_permission`, `make_plugin_db`

**Trackify (referência — repositório do Nexus, NÃO muda neste plano):**
- `/opt/nexus/trackify/prisma/schema.prisma` — schema completo
- `/opt/nexus/trackify/server/modules/contacts/contacts.service.ts` — por que `?search=` não serve (§5.1)
- `/opt/nexus/trackify/server/modules/events/events.controller.ts`
- `/opt/nexus/trackify/server/modules/ingestion/{ingestion.controller,pipeline,adapter,auth-validator}.ts` — **caminho de escrita da v2** (§10)

**Testes:**
- `tests/test_trackify_journey.py` (novo) — variantes de telefone, derivação de assinatura, tolerância a `dd/mm/yyyy` e a `"system"`
