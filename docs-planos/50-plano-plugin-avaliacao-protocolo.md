# Plano 50 — Avaliação de protocolo via página externa (Cloudflare Worker)

## Objetivo

Fechar o **round-trip da avaliação** do plugin `protocolos`: hoje ele já envia, ao
FINALIZAR um protocolo, um link com o código do protocolo (`id_protocol`) e o
`assignee_id` do atendente — mas esse código **não é persistido em lugar nenhum**,
então é impossível, quando o cliente abre a página de avaliação, descobrir o
atendente / contato / conversa daquele código, nem gravar a nota de volta.

Este plano cobre **só o lado WhatsBot** (o plugin). A página de avaliação (rating
1–5 + sugestão) roda num **Cloudflare Worker** e será feita depois. O Worker chama
o WhatsBot **server-side** (guarda a URL do WhatsBot como env própria) — logo **não
há mudança no core** (sem CORS, sem injeção de domínio; o `public_base_url` já
existe e continua disponível para outras automações).

## Decisões (confirmadas com o usuário)

| # | Decisão | Escolha |
|---|---------|---------|
| Q1 | Como o Worker conhece a URL do WhatsBot | **Worker guarda a URL** — sem mudança no core |
| Q2 | Chave da URL de avaliação | **O próprio `id_protocol`** (formato atual `DDMMYYYY-HHMMSS.mmm-RRRRR`); mitigado por **rate-limit por IP + uso único** |
| Q3 | O que fazer com a nota recebida | **Gravar nas tabelas do plugin** (schema decidido abaixo) |
| Q4 | Qual link roda o round-trip | **Só o link do cliente** (normal/WhatsApp); o link privado interno segue como está |

### Schema (delegado ao dev): tabela dedicada, não colunas

Tabela nova **`plugin_protocolos_avaliacoes`** (migration `015`), não colunas em
`plugin_protocolos_protocolos`, porque:

1. A linha nasce **no fechamento**, com o `id_protocol` + snapshot de
   atendente/contato/conversa/canal, **antes** de existir qualquer nota — uma coluna
   no protocolo não representa o estado "pendente, ainda não avaliado".
2. Um protocolo pode **reabrir → refechar** → novo link → **N avaliações** ao longo
   do tempo. Coluna só guardaria a última; tabela guarda o histórico (1 linha por
   fechamento/link).
3. Segue o padrão do plugin (tabelas auto-contidas + `_attach_*` para exibir sem
   join / sem inchar a tabela principal).

Para "aparecer no protocolo", a última avaliação **respondida** é anexada em
`_attach_avaliacao` (batch, igual a `_attach_latest_atendimento`) → o dict do
protocolo ganha `avaliacao: {nota, sugestao, answered_at}`.

## Entregas

### 1. Migration `015_avaliacoes.sql`

```
plugin_protocolos_avaliacoes (
  id                INTEGER PK AUTOINCREMENT,
  id_protocol       TEXT UNIQUE NOT NULL,   -- chave da URL (código exibido)
  protocolo_id      INTEGER NOT NULL,
  contact_id        INTEGER NOT NULL,
  conversation_id   INTEGER,                -- conversa mais recente no fechamento
  channel_id        TEXT NOT NULL DEFAULT '',
  assignee_user_id  INTEGER,
  assignee_name     TEXT NOT NULL DEFAULT '',   -- snapshot p/ o GET (nome do atendente)
  contact_phone     TEXT NOT NULL DEFAULT '',
  contact_name      TEXT NOT NULL DEFAULT '',
  nota              INTEGER,                -- NULL até responder (1..5)
  sugestao          TEXT NOT NULL DEFAULT '',
  answered_at       DOUBLE PRECISION,       -- NULL = pendente (gate de uso único)
  answered_ip       TEXT NOT NULL DEFAULT '',
  created_at        DOUBLE PRECISION NOT NULL,
  updated_at        DOUBLE PRECISION NOT NULL
)
-- índices: UNIQUE(id_protocol); INDEX(protocolo_id); INDEX(answered_at)
```

### 2. `logic.py`

- `_register_avaliacao(at, *, id_protocol, conversation_id, channel_id) -> None` —
  INSERT da linha no fechamento (snapshot). Retry em colisão de `id_protocol`
  (astronomicamente raro). Best-effort, nunca levanta.
- Alterar `send_protocol_on_close`: gerar `id_protocol` **uma vez**, registrar a
  linha (só p/ o link **normal**), e continuar anexando os params à URL como hoje.
- `get_avaliacao_public(id_protocol) -> dict | None` — devolve
  `{atendente, protocolo, ja_avaliado}` (nome do atendente resolvido do snapshot,
  fallback `user_repo`). `None` → 404.
- `record_avaliacao(id_protocol, nota, sugestao, ip) -> tuple[dict|None, str|None]` —
  valida `nota ∈ 1..5`, corta `sugestao` (cap ~2000), **uso único** (rejeita se
  `answered_at` já setado), grava, `_attach`/`_broadcast_changed` p/ o Kanban
  atualizar ao vivo.
- `_attach_avaliacao(items)` — batch da última avaliação respondida por protocolo.
  Chamado em `_hydrate_protocolos`.
- Rate-limiter simples em memória por IP (janela deslizante) reutilizando o padrão
  `_client_ip` (XFF rightmost) do plugin `website`.

### 3. `routes.py` (endpoints públicos, sob `/public/`, isentos de auth)

- `GET  /api/plugins/protocolos/public/avaliacao/{id_protocol}` → consulta atendente.
- `POST /api/plugins/protocolos/public/avaliacao/{id_protocol}` `{nota, sugestao}` →
  grava a nota. Ambos com rate-limit por IP; resposta `{ok, data|error}`.

### 4. `plugin.yaml`

- Bump `1.12.0` → `1.13.0`.

### 5. Testes

- `tests/test_avaliacao_protocolo.py` — registrar token → GET (atendente correto,
  `ja_avaliado=false`) → POST nota válida → GET (`ja_avaliado=true`) → POST de novo
  (rejeitado, uso único) → POST nota inválida (rejeitado). Espelha o padrão de
  `tests/test_website_widget.py`.

## Contrato para a página (Cloudflare Worker) — referência da fase 2

- Link enviado: `<url-do-worker>?id_protocol=<código>&assignee_id=<id>`
- Worker → `GET  {WHATSBOT_URL}/api/plugins/protocolos/public/avaliacao/{id_protocol}`
  → `{ok, data:{atendente, protocolo, ja_avaliado}}`
- Worker → `POST {WHATSBOT_URL}/api/plugins/protocolos/public/avaliacao/{id_protocol}`
  body `{nota:1..5, sugestao:""}` → `{ok:true}` | `{ok:false, error}`

## Fora de escopo

- Página da Cloudflare (fase 2).
- Round-trip do link privado interno (Q4 = só o do cliente).
- Mensagem de agradecimento / aviso de sistema / group-by nativo do Kanban por nota
  (não selecionados; a nota fica visível no dict do protocolo via `_attach_avaliacao`).
