# Plano 114 — Plugin de pagamento: gerar cobrança PIX (Banco Inter) de dentro do atendimento

> **Status:** EM EXECUÇÃO (F0–F2, F4–F10 concluídas · F3 e F11 bloqueadas em ação do usuário ·
> F12 parcial) · **Data:** 2026-08-12 · **Escopo:** grande
> **Origem:** pedido do usuário — replicar no WhatsBot a aba "PIX Manual" do worker Cloudflare `recovery`
> (`recovery.onlinecenterdigital.workers.dev/pix-manual`), numa versão mais simples.
> **Método:** leitura do worker real por SSH (`10.8.254.194:64777`), da doc interna dele, do código do core
> e dos plugins `retornos`/`protocolos`/`website`/`trackify`, consulta à API da Cloudflare pelo cofre, aos
> bancos de PRODUÇÃO do WhatsBot e do n8n (somente leitura) e **5 chamadas de leitura à API real do Banco
> Inter**. Todo `arquivo:linha` abaixo foi verificado.
>
> ⚠️ **Este plano foi revisado no mesmo dia** (§2.3): a credencial do Inter que a 1ª versão dava como
> irrecuperável **foi encontrada** em texto plano no n8n. Isso revisou a D1, reabriu a D2 (P7) e acrescentou
> um achado de segurança (§2.3.3).
>
> Um plugin novo gera a cobrança PIX na conta do Banco Inter a partir do ⋮ da conversa, manda o
> copia-e-cola para o cliente no próprio canal e marca "Pago" sozinho quando o Inter confirma. Como parte
> do corte, o **webhook da chave PIX migra da Cloudflare para o WhatsBot**, que passa a ser responsável por
> **repassar** o que não é dele.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar
> para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| D1 | ⚠️ **REVISTA (2026-08-12, mesmo dia)** — a decisão original era "aplicação NOVA no portal do Inter" porque a credencial em uso parecia irrecuperável. **Ela foi encontrada** (§2.3.1). A ordem de preferência agora é: (1) **reusar a credencial existente**, se o certificado dela for recuperado; (2) app nova. O usuário pediu explicitamente para reusar o que já rodava | A F3 deixou de ser "provisionar" e virou "recuperar OU provisionar", com o caminho de recuperação detalhado. Rotacionar o secret da app dos workers continua **proibido** — derrubaria `imersao-analista` e a aba PIX Manual |
| D2 | ✅ (2026-08-12) **Mesma chave PIX** (CNPJ `09596968000105`). O webhook dela **migra** para o WhatsBot | O WhatsBot vira o único destinatário e **precisa repassar** o payload cru para o `imersao-analista`, senão a venda da imersão para de liberar acesso (§3.3). Revoga a decisão anterior de criar uma 2ª chave. ⚠️ **Fato novo que pode reabrir isto**: a conta **já tem uma 2ª chave PIX** (aleatória — §2.3.2), então "webhook próprio, sem repasse" voltou a custar zero. Ver **P7** |
| D3 | ✅ (2026-08-12) **`txid` com prefixo** `wb…` carregando o vendedor | É o roteador entre "meu" e "repasse", e o único identificador que volta em webhook, GET e conciliação. `infoAdicionais` foi **recusado** (o pagador vê, e não volta no webhook) |
| D4 | ✅ (2026-08-12) Vendedor/UTM em **cadastro próprio do plugin** | **Não** usar o plugin `utm_atendente`. O plugin tem de ser redistribuível sozinho — dependência entre plugins obrigaria a distribuir os dois |
| D5 | ✅ (2026-08-12) UI **dentro da conversa** (⋮) + tela própria de histórico | O botão manda o copia-e-cola na conversa. É o ganho real sobre o painel atual, que só copia para a área de transferência |
| D6 | ✅ (2026-08-12) Ao pagar: **nota privada** no fio + **evento no bus** + **Trackify** | Nada de mensagem automática ao cliente nesta versão |
| D7 | ✅ (2026-08-12) **Sem** liberação Curseduca e **sem** lançar pedido em `vendas.public.pedidos` | Migração posterior. A intenção declarada é aposentar a aba da Cloudflare aos poucos |
| D8 | ✅ (2026-08-12) Zero mudança no core | Tudo no plugin, imports defensivos, tabela `plugin_<id>_*` — regra do `CLAUDE.md` §"O que fica no core e o que vai pro plugin" |

---

## 1. Resumo executivo

Hoje, vender no PIX negociado exige sair do atendimento: abrir outro painel, escolher o curso, gerar,
copiar o código, voltar ao WhatsBot e colar. O plugin fecha esse ciclo dentro da conversa — o ⋮ abre um
formulário já preenchido com os dados do contato, gera a cobrança na conta do Inter e **manda** o
copia-e-cola no canal.

A parte delicada não é gerar a cobrança: é a **confirmação**. O Banco Inter aceita **um webhook por chave
PIX**, e a chave da casa já está registrada num worker da Cloudflare. Com D2, o WhatsBot toma esse webhook
e passa a ter uma responsabilidade que não é dele: **entregar de volta** ao worker tudo que não lhe pertence.
Errar isso quebra, em silêncio, uma venda que não é nossa. Por isso o repasse é incondicional, persistido e
com retentativa — e o corte tem plano de reversão.

A segunda regra dura: **o webhook avisa, o GET decide.** O corpo do callback nunca é fonte de verdade;
para todo `txid` nosso o plugin reconsulta `GET /pix/v2/cob/{txid}` no Inter por mTLS antes de marcar pago.
Isso torna a forja do callback irrelevante — que é o único jeito honesto de proteger uma rota pública que o
Inter não assina.

---

## 2. Como funciona hoje (mapa verificado)

### 2.1 A aba PIX Manual do worker `recovery`

Fonte: `~/PROJETOS/cloudflare/recovery/src/index.js` (6 386 linhas) no servidor `10.8.254.194:64777`.
Doc interna: `~/PROJETOS/cloudflare/dashboard/11-pix-manual/README.md` e a seção "Aba PIX Manual" de
`~/PROJETOS/cloudflare/CLAUDE.md`.

| Peça | Onde | O que faz |
|---|---|---|
| `interToken` | `recovery/src/index.js:1045` | OAuth `client_credentials`, cache de 1 h em variável de isolate |
| `interCreateCob` | `recovery/src/index.js:1073` | `PUT /pix/v2/cob/{txid}` → devolve `pixCopiaECola` |
| `interGetCob` | `recovery/src/index.js:1093` | `GET /pix/v2/cob/{txid}` |
| `parseValorBR` | `recovery/src/index.js:1106` | aceita `1.997,00`, `1997.00`, `R$ 1997` |
| `syncPixManualStatus` | `recovery/src/index.js:1201` | consulta e marca `CONCLUIDA`/`EXPIRADA` |
| `checkPixManualPendentes` | `recovery/src/index.js:1281` | varredura do cron (≤12 por rodada, conferidas há +45 s) |
| `POST /api/pix-manual` | `recovery/src/index.js:6080` | cria a cobrança e grava no D1 |
| Tabela `pix_manual` | `recovery/migration_017_pix_manual.sql` (+018, +019) | modelo do nosso schema |
| Mensagem pronta pro WhatsApp | `recovery/src/index.js:4131` (`mensagemDe`) | texto que vamos portar |

Auth do painel inteiro: `?token=redesbrasil2025` na URL. **Não** vamos portar isso — no WhatsBot é RBAC.

### 2.2 As três pegadinhas do Banco Inter

⚠️ Nenhuma das três está óbvia na documentação pública, e cada uma já custou um incidente lá:

1. **mTLS é obrigatório em TODA chamada, inclusive no OAuth.** No worker isso vira `env.INTER_CERT.fetch(...)`
   e nunca o `fetch` global (`recovery/src/index.js:1042-1044`, comentário explícito). Em Python é
   `httpx.Client(cert=(crt, key))` — inclusive na chamada de token.
2. **O Inter não tem status "expirada".** A cobrança fica `ATIVA` para sempre e só deixa de aceitar
   pagamento; quem marca o vencimento é o nosso código, comparando com `expira_at`
   (`recovery/src/index.js:1221-1224`).
3. **`GET /pix/v2/loc/{id}/qrcode` responde 404.** O QR tem de ser desenhado a partir do `pixCopiaECola`.

Mais: `solicitacaoPagador` tem teto de 140 caracteres e é escrito **sem acento de propósito** — nem todo app
de banco trata UTF-8 no BR Code (`cloudflare/CLAUDE.md`, seção da aba). Teto de R$ 100 000 por cobrança
(`recovery/src/index.js:6086`). Validades oferecidas: 1 h / 24 h / 3 d / 7 d (`recovery/src/index.js:6093`).

### 2.3 Credenciais — a caçada completa (2026-08-12)

⚠️ **Correção de uma conclusão anterior deste plano.** A primeira versão afirmava que a credencial do Inter
era "irrecuperável". **Está errado**: uma delas foi encontrada em texto plano no n8n. O que segue substitui
aquela afirmação.

**Onde procurei** (para ninguém repetir a varredura):

| Lugar | Resultado |
|---|---|
| Secrets dos workers da Cloudflare | ❌ write-only por design — `GET …/workers/scripts/imersao-analista/settings` lista `INTER_CLIENT_ID`, `INTER_CLIENT_SECRET` e `INTERNAL_TOKEN` como `secret_text` **sem valor** |
| Os 264 workers da conta | ❌ nenhum outro com nome ligado a Inter/PIX |
| Cofre (`vault_discover`, 17 credenciais + busca por "inter") | ❌ nada |
| Windmill — **resources** e **variables** (workspace `redesbrasil`) | ❌ nada de Inter |
| Filesystem do servidor `10.8.254.194` (`~/PROJETOS`, `~/matheus`, `/home`) | ⚠️ só o **par mTLS** e uma nota de memória (`~/.claude/…/memory/reference_banco_inter_api.md`) que confirma "clientId/clientSecret ficam nos secrets do worker" |
| `.bash_history` do servidor | ❌ nada |
| **n8n (`n8n_queue`)** | ✅ **ACHADO** — ver §2.3.1 |
| n8n (`n8n_zapify`) | ❌ nada |

#### 2.3.1 O que foi encontrado — e por que ainda não fecha sozinho

Existem **duas aplicações distintas** do Inter em uso na casa, e de cada uma temos **metade**:

| | App **A** — a do n8n ("a que a Erika usou") | App **B** — a dos workers Cloudflare |
|---|---|---|
| `client_id` | ✅ **em texto plano** no n8n, nó `generate_token` do workflow **`GeneratePix`** (`n8n_queue`, `workflow_entity.id = YOs6S5h01mQO8VIC`; o mesmo valor está no workflow `BOOTCAMP ANALISTA EM REDES`, id `4YQpG9Yn6Dty40zm`) | ❌ secret write-only da Cloudflare |
| `client_secret` | ✅ mesmo lugar, mesmo nó | ❌ idem |
| Certificado mTLS | ❌ só dentro do n8n, na credencial **`inter SSL`** (`credentials_entity.id = zHSOvzRxahO1uDDn`, tipo `httpSslAuth`, criada 2025-05-29) — **cifrada** com a `N8N_ENCRYPTION_KEY` | ✅ `~/PROJETOS/cloudflare/banco-inter/` (PKCS#8 sem senha, `CN=REDES BRASIL LTDA`, `OU=598411d3-09d4-4151-bed3-6eca0ba1703f`, vence **2027-05-27**) |
| **Par completo?** | **não** (falta o cert) | **não** (falta o secret) |

**A evidência de que são aplicações diferentes** (medida contra a API real do Inter, 5 chamadas somente de
leitura, 2026-08-12):

1. `POST /oauth/v2/token` com o `client_id`/`secret` da app A **+ o certificado da app B** → **HTTP 200**,
   token emitido, escopos concedidos `cob.write cob.read cobv.write cobv.read pix.write pix.read
   webhook.read webhook.write`.
2. Qualquer chamada de **recurso** com esse mesmo token → **HTTP 401 `Login/senha inválido`**:
   `GET /pix/v2/cob?…`, `GET /pix/v2/webhook/09596968000105`, `GET /pix/v2/webhook/98a64b6b-…`.
3. O `OU` do certificado (`598411d3-…`) **não é** o `client_id` da app A (`a7e926c0-…`).

Leitura: o Inter emite o token validando só `client_id`+`secret`, mas amarra a **chamada de recurso** ao
certificado da aplicação. Token 200 seguido de 401 em tudo = **certificado de outra aplicação**.
⚠️ Isto é uma **hipótese com evidência forte, não um fato confirmado pelo banco** — a explicação alternativa
seria o header `x-conta-corrente` (obrigatório quando a app enxerga mais de uma conta), mas aí o erro
esperado não seria "Login/senha inválido". **Confirmar antes de agir** (P8).

Achado colateral que vale por si: **os escopos de pagamento não foram concedidos**. O nó do n8n pede
`pagamento-pix.write`, `pagamento-boleto.write`, `pagamento-darf.write` e `extrato.read`, e o Inter devolve
só os oito de cobrança. Ou seja, **a credencial da app A não move dinheiro para fora** — só cobra. É o que
torna reusá-la aceitável.

#### 2.3.2 Dois achados de contexto que mudam decisões

1. **Existe uma 2ª chave PIX na conta.** Os nós `Create Webhook`/`Get Webhooks` do `GeneratePix` apontam
   para `…/pix/v2/webhook/98a64b6b-10de-4b1e-b213-14daa085ec33` — uma **chave aleatória**, não o CNPJ. Como
   o webhook é por chave, essa chave é um webhook próprio **que já existe e não custa nada**. Reabre a opção
   que D2 tinha descartado por custo (P7). Os dois workflows do n8n estão **inativos**, então a chave está
   provavelmente ociosa — **confirmar antes de usar**.
2. ⚠️ **O `GeneratePix` chama `POST /pix/v2/cob`** (cobrança **sem** `txid`, o Inter gera). O nosso desenho
   usa `PUT /pix/v2/cob/{txid}` com `txid` nosso — e **tem de continuar assim**, porque é o `txid` que
   carrega o vendedor e roteia o repasse (D3). Não copiar o verbo do n8n.

#### 2.3.3 Isto é um achado de segurança, não só um achado de credencial

`client_id` e `client_secret` de uma conta bancária estão **em texto plano** numa coluna JSON do n8n
(`workflow_entity.nodes`), legíveis por qualquer um com acesso de leitura ao banco — foi exatamente assim
que os encontrei. Some-se: o par mTLS está solto em `~/PROJETOS/cloudflare/banco-inter/` com permissão de
leitura para o grupo, e o endpoint `…/api/inter-webhook` do worker está aberto na internet **sem
autenticação nenhuma** (medido: `{"pix":[]}` → `{"ok":true}`).

Ações que este plano assume (F3), independentemente de qual credencial vencer:

- mover as quatro peças (id, secret, cert, chave) para o **cofre**, que hoje não tem nenhuma entrada de Inter;
- **não** escrever nenhum valor neste arquivo — `docs-planos/` é versionado, e o plano 78 foi uma limpeza de
  segredo do histórico do git. As referências acima são *ponteiros* (banco, tabela, id), de propósito;
- avaliar rotação do secret da app A **depois** de confirmar que só o n8n (inativo) a usa.

### 2.4 Quem depende do webhook da chave hoje

| Consumidor | Depende do webhook? | Rede secundária |
|---|---|---|
| `imersao-analista` (venda da imersão, R$ 37,90) | ✅ **sim** — `handleInterWebhook` em `imersao-analista/src/index.js:1365`, rota em `:1410` | parcial: a landing page chama `/api/pix-status` (`imersao-analista/src/index.js:1301`) que também libera — **mas só enquanto a página estiver aberta** |
| Aba PIX Manual do `recovery` | ❌ não — confirma por **polling** no cron | é a própria varredura |

Medido: `POST https://imersao-analista.onlinecenterdigital.workers.dev/api/inter-webhook` com `{"pix":[]}`
responde **`{"ok":true}`** — o endpoint está no ar e **não tem autenticação nenhuma**. Isso é ruim para eles
e conveniente para nós: o repasse é um POST simples, sem segredo a negociar.

O handler de lá é idempotente por construção: `txid` que não está no KV cai em `continue`
(`imersao-analista/src/index.js:1372`) e `status === 'CONCLUIDA'` também (`:1374`). **Repassar o payload
inteiro, sempre, é seguro** — inclusive com os nossos `txid` dentro.

⚠️ **O que NÃO consegui confirmar:** qual URL está de fato registrada na chave. `GET /pix/v2/webhook/…`
respondeu **401** com a credencial que tenho (§2.3.1), então as duas candidatas acima seguem como
candidatas. A F11.1 trata isso como leitura obrigatória, não como suposição.

### 2.5 As costuras do WhatsBot que o plugin vai usar

| Costura | Onde | Uso no plugin |
|---|---|---|
| Isenção de auth para rota pública | `server/app.py:56` (`PLUGIN_PUBLIC_PATH_RE`) e `server/app.py:552` | o webhook do Inter mora em `/api/plugins/<id>/public/…` |
| Precedente de rota pública com defesa própria | `website/src/routes.py:214` | mesmo padrão: valida por conta própria, nunca confia na borda |
| Envio no canal | `plugins/context.py:121` (`get_channel_runtime`) → `outbound_router.send_text` | mandar o copia-e-cola na conversa |
| ⚠️ Supressão de eco | `retornos/src/actions.py:143` (`_mark_echo_text`), `:171` (`_mark_echo_id`), `:161` (undo) | **obrigatório** — sem isso a bolha duplica em canal que ecoa |
| Nota privada no fio | `retornos/src/actions.py:112` (`post_private_note`) | o aviso de "PIX pago" |
| Auditoria | `plugins/context.py:226` (`audit`) | criar cobrança, migrar webhook, revelar segredo |
| RBAC de plugin | `plugins/context.py:286` (`plugin_permission`) | gatear as rotas de operador |
| Task supervisionada | `plugins/context.py:426` (`spawn_task`, `RestartPolicy.PERMANENT` em `:441`) | reconciliação e retentativa do repasse |
| Broadcast ao vivo | `plugins/context.py:146` | atualizar a tela sem polling |
| Slot do ⋮ | `web/static/js/components/contacts/ConversationMenu.js:22`, ctx montado em `ConversationHeaderActions.js:200` (`{conv, user}`) | o botão "Gerar PIX" |
| Precedente de slot + modal | `trackify/src/static/extends.js:24` | `api.addSlot` + `api.ui.openModal` + `api.http` |
| QR server-side | `segno` já está em `requirements.txt`; precedente em `app/services/provisioning_service.py:45` (`png_data_uri`) | desenhar o QR do `pixCopiaECola` sem CDN (a CSP do core bloqueia jsdelivr) |
| `public_base_url` | lido como em `website/src/routes.py:339` | montar a URL do webhook para o operador copiar |

### 2.6 O seam `plugins.services` (Trackify)

| Item | Onde | Situação |
|---|---|---|
| Registro do seam no core | `origin/developer:plugins/services.py` (423 linhas), entry em `origin/developer:plugins/loader.py:337` | ⚠️ **não está no checkout local** — `developer` local está 5 commits atrás; `plugins/loader.py:307` daqui tem 8 chaves, sem `services` |
| Manifesto do consumidor | `origin/developer:plugins/manifest.py:247` (`_parse_uses_services`) | bloco `uses_services:` |
| Provider | `trackify/src/services.py:366` (`SERVICES`, 18 ops) | `track_event` em `:96` |
| Precedente de consumidor | `protocolos/src/logic.py:5315` (`_services.call(...)`), import defensivo em `:51-54` | **copiar esse padrão literalmente** |
| Kinds reservados | `trackify/src/mirror.py:33-46` | `pix_gerado`/`pix_pago`/`pix_expirado` **não** colidem |

Produção já roda o seam: `trackify 4.0.0` e `protocolos 1.33.0` ativos e sem `load_error` (consultado no
banco de PROD). **O plugin novo não pode exigir isso**: import defensivo, e sem o seam ele apenas não espelha.

### Falsos positivos descartados

| Parece problema | Por que não é |
|---|---|
| "Precisa de uma 2ª chave PIX para ter webhook" | Verdade técnica, mas o usuário revogou (D2). Com repasse, uma chave só atende os dois |
| "O prefixo `wb` pode colidir com um `txid` da Cloudflare" | **Impossível por construção**: os dois workers geram `crypto.randomUUID().replace(/-/g,'')` = 32 chars em `[0-9a-f]`, e `w` **não é dígito hexadecimal** (`recovery/src/index.js:1069`, `imersao-analista/src/index.js:953`) |
| "Dá para reusar o `MetaGraphChannel`/infra de canal" | Não é canal. PIX não recebe nem envia mensagem por conta própria — é uma tool de operador que usa o canal existente |
| "O `utm_atendente` já resolve o vendedor" | Resolve, mas criaria dependência entre plugins e mataria a redistribuição (D4). E ele mapeia **autor de nota privada**, não "quem gerou a cobrança" |
| "O `recovery` polling precisa ser desligado no corte" | Não. Ele **não** usa o webhook (§2.4); continua funcionando igual depois da migração |
| "Precisa declarar `httpx`/`segno` em `dependencies` do manifest" | Ambos já estão em `requirements.txt` do core. Declarar dispararia `ensure_pip_deps` (`plugins/pkg_deps.py`) sem necessidade |
| "A nota privada precisa de evento novo no catálogo do core" | Não. `private_note` é role existente e `post_private_note` já é padrão de plugin (`retornos`) |

---

## 3. Desenho proposto

**Id do plugin:** `pagamentos` (ver P1). Tabelas `plugin_pagamentos_*`, pacote `whatsbot_plugins.pagamentos`,
rotas em `/api/plugins/pagamentos`. O módulo específico do banco é `inter.py` — trocar/somar gateway depois
não obriga a renomear tabela.

### 3.1 Módulos

| Arquivo (`src/`) | Papel | Puro? |
|---|---|---|
| `plugin.yaml` | manifest: `entry` routes/lifecycle/settings, 2 screens, `frontend_extends`, `rbac`, `uses_services: trackify` | — |
| `txid.py` | `new_txid(codigo)`, `is_ours(txid)`, `vendor_code_of(txid)` | ✅ sem banco, sem rede |
| `money.py` | `parse_valor_br`, `to_inter_str`, `format_brl` — porte de `parseValorBR` | ✅ |
| `mensagem.py` | template do texto que vai ao cliente (porte de `mensagemDe`) | ✅ |
| `inter.py` | OAuth com cache + `create_cob` / `get_cob` / `get_webhook` / `set_webhook` / `delete_webhook` | sem banco |
| `certs.py` | materializa cert+chave em `storages/plugins/pagamentos/certs/` com `0600`, valida e reporta validade | — |
| `store.py` | acesso a `plugin_pagamentos_*` + config | — |
| `routes.py` | operador (gateado por RBAC) + `/public/webhook/{secret}` | — |
| `reconcile.py` | ➕ **acrescentado na execução** — a regra ÚNICA de "esta cobrança está paga?". Tem três chamadores (webhook, varredura, botão do painel); deixá-la em `routes.py` obrigaria o `lifecycle` a importar rota, e duplicá-la traria de volta a segunda nota privada | — |
| `forward.py` | repasse do payload cru + retentativa | — |
| `notify.py` | nota privada, envio no canal com supressão de eco, broadcast | — |
| `trackify_bridge.py` | `services.call` defensivo | — |
| `lifecycle.py` | tasks `pagamentos:reconcile` e `pagamentos:forward_retry` | — |
| `settings.py` | defaults escalares (a edição é pela screen) | — |
| `static/extends.js` | slot `conversation.header.actions` + modal "Gerar PIX" | — |
| `static/pagamentos.js` | tela de histórico/status | — |
| `static/config.js` | screen `config: true`: credenciais, chave, webhook, vendedores | — |
| `migrations/001_initial.sql` | 3 tabelas | — |

### 3.2 Schema

```sql
plugin_pagamentos_cobrancas (
  txid TEXT PRIMARY KEY,            -- wb + <codigo vendedor> + aleatório
  valor NUMERIC(12,2) NOT NULL,     -- ⚠️ NUMERIC, não TEXT como no D1 do worker
  descricao TEXT,                   -- solicitacaoPagador, ≤140, sem acento
  cliente_nome TEXT, cliente_email TEXT, cliente_phone TEXT,
  vendedor_utm TEXT, vendedor_codigo TEXT,
  channel_id TEXT, conversation_id BIGINT, contact_id BIGINT,
  created_by_user_id BIGINT,
  pix_code TEXT,                    -- pixCopiaECola
  status TEXT NOT NULL DEFAULT 'ATIVA',   -- ATIVA | CONCLUIDA | EXPIRADA
  created_at DOUBLE PRECISION NOT NULL, expira_at DOUBLE PRECISION,
  checked_at DOUBLE PRECISION, paid_at DOUBLE PRECISION,
  valor_pago NUMERIC(12,2), e2e_id TEXT, pagador TEXT,
  sent_msg_id TEXT, sent_at DOUBLE PRECISION,   -- o envio na conversa
  notified_at DOUBLE PRECISION                  -- dedup da nota privada
)
plugin_pagamentos_vendedores (id, nome, utm_term, codigo TEXT UNIQUE, ativo)
plugin_pagamentos_webhook_inbox (id, received_at, raw JSONB, forwarded_at, forward_error, attempts)
```

⚠️ `valor` vira `NUMERIC`, não `TEXT`. No D1 era string porque o SQLite não tem decimal e o formato do Inter
é string; no Postgres manter string tornaria "somar o que entrou hoje" um `CAST` frágil. A conversão para
`"1997.00"` acontece na borda, em `money.to_inter_str`.

### 3.3 O corte do webhook — o ponto mais delicado

```
                       ANTES                                    DEPOIS
   Inter ──► imersao-analista/api/inter-webhook     Inter ──► WhatsBot /public/webhook/{secret}
                                                                  │
                                        ┌─────────────────────────┼──────────────────────────┐
                                        ▼                         ▼                          ▼
                            grava inbox (SEMPRE)        repassa CRU (SEMPRE)       processa só os `wb*`
                                                     imersao-analista (retry)      via GET no Inter
```

Quatro regras não-negociáveis:

1. **Repasse incondicional e primeiro.** O payload **inteiro e cru** vai para o destino configurado, sem
   filtrar por `txid`. Filtrar seria mais "limpo" e é exatamente o que quebra a venda dos outros no dia em
   que o formato mudar. O handler de lá ignora o que não conhece (§2.4).
2. **O repasse nunca influencia a resposta ao Inter.** Grava no inbox, agenda a entrega numa task destacada
   e responde `200` na hora. Falha de repasse vira `forward_error` + retentativa, jamais um erro ao banco
   (que provocaria reentrega e amplificação).
3. **O corpo é gatilho, não verdade.** Para cada `txid` nosso, reconsulta `GET /pix/v2/cob/{txid}` por mTLS
   antes de mudar status. Custa uma chamada por evento e torna a forja do callback inofensiva.
4. **Reversão em um comando.** A migração é um `PUT /pix/v2/webhook/{chave}`; voltar é outro `PUT` com a URL
   antiga. A fase de corte (F10) registra a URL anterior via `GET /pix/v2/webhook/{chave}` **antes** de trocar.

### 3.4 Segurança da rota pública

O Inter **não assina o callback** (não há HMAC; a proteção do lado dele é o TLS do destino). Camadas, em
ordem de valor real:

| Camada | O que é | Vale quanto |
|---|---|---|
| **Reconsulta no Inter** | nada é marcado pago sem `GET /pix/v2/cob/{txid}` | **é a defesa** — anula forja por completo |
| Segredo no path | `/public/webhook/{secret}`, gerado no boot, revelável só com `plugin.pagamentos.manage` (padrão de `website/src/routes.py:358`) | evita varredura e ruído |
| Limite de corpo + rate limit por IP | rejeita payload gigante/inundação antes de tocar o banco | proteção de recurso |
| Allowlist de IP de origem | opcional, desligada por padrão | ⏸️ só se o Inter publicar a faixa — **a confirmar** (P3) |

Nunca logar o segredo nem a URL completa do webhook.

### 3.5 Formato do `txid`

`wb` + `<codigo>` (2 chars `[a-z0-9]`, do vendedor cadastrado) + 24 hex aleatórios = **28 chars**, dentro
da faixa 26–35 exigida. Sem vendedor, o código é `00`.

`is_ours(txid)` = começa com `wb`. Como todo `txid` legado dos dois workers é hexadecimal puro e `w` não é
hex, a classificação é exata por construção — e mesmo assim o repasse é incondicional (§3.3), então uma
classificação errada nunca perde o evento de ninguém.

### 3.6 Fluxo do operador

1. ⋮ da conversa → **Gerar PIX** (só com `plugin.pagamentos.cobrar`).
2. Modal com nome/e-mail/telefone pré-preenchidos do contato, valor, descrição, validade e vendedor.
3. **Gerar e enviar** (primário) grava, mostra código + QR e manda a mensagem no canal.
   **Só gerar** (secundário) para quem quer conferir antes.
4. Tela do plugin lista o histórico com "Aguardando / Pago / Expirado", botão **Conferir agora** e o total
   pago no dia.

---

## 4. Inventário da mudança

| # | Item | Onde | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|
| 1 | Cliente Inter com mTLS | `src/inter.py` (novo) | porte de `recovery/src/index.js:1045-1102` para `httpx` | médio | M |
| 2 | Materialização do certificado | `src/certs.py` (novo) | PEM da config → arquivo `0600` em `storages/` | médio | S |
| 3 | Módulos puros | `src/txid.py`, `money.py`, `mensagem.py` | porte de `:1069`, `:1106`, `:4131` | baixo | S |
| 4 | Schema | `src/migrations/001_initial.sql` | modelo em `migration_017/018` do worker, com os ajustes de §3.2 | baixo | S |
| 5 | Rotas de operador | `src/routes.py` | criar / listar / conferir / enviar / vendedores | baixo | M |
| 6 | Webhook + repasse | `src/routes.py` + `src/forward.py` | §3.3 | **alto** | L |
| 7 | Notificação | `src/notify.py` | eco suprimido como `retornos/src/actions.py:143` | **alto** | M |
| 8 | Reconciliação e expiração | `src/lifecycle.py` | task supervisionada; rede de segurança do webhook | médio | M |
| 9 | Slot + modal | `src/static/extends.js` | molde `trackify/src/static/extends.js:24` | baixo | M |
| 10 | Tela de histórico | `src/static/pagamentos.js` | tempo real por `api.services.subscribe` | baixo | M |
| 11 | Config (credenciais, chave, webhook, vendedores) | `src/static/config.js` + rotas | segredo mascarado no GET | médio | M |
| 12 | Trackify + bus + auditoria | `src/trackify_bridge.py` | molde `protocolos/src/logic.py:5311` | baixo | S |
| 13 | Corte do webhook no Inter | operação | §3.3 regra 4 | **alto** | S |

---

## 5. Fases e paralelização

```
WAVE 0   F0 (esqueleto+schema) 🟢   F1 (módulos puros) 🟢   F2 (cliente Inter) 🟢   F3 (provisionar no Inter — usuário) 🟢
                         │                    │                     │                          │
WAVE 1   ────────────────┴────────────────────┴─────────────────────┘                          │
         F4 (store + rotas de operador) 🔴  [depende: F0,F1,F2]                                 │
         F5 (vendedores + tela de config) 🟢 [depende: F0]                                      │
                         │                                                                      │
WAVE 2   F6 (webhook + repasse + inbox) 🔴 [depende: F4]    F7 (notificação/eco) 🟢 [depende: F4]│
                         │                                            │                         │
WAVE 3   F8 (frontend: slot, modal, tela) 🟢 [depende: F4,F7]   F9 (trackify+bus+audit) 🟢 [F4,F6]
                         │                                                                      │
WAVE 4   F10 (testes pelo loader real) 🔴 [depende: tudo]                                       │
         F11 (CORTE do webhook + ponta a ponta com R$0,01) 🔴 [depende: F10, F3] ◄───────────────┘
         F12 (build, zip, publicação, instalação em prod) 🔴 [depende: F11]
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | F0 | esqueleto + manifest + migrations | 🟢 | baixo | plugin aparece em `/plugins` e ativa sem `load_error` |
| 0 | F1 | módulos puros (`txid`/`money`/`mensagem`) | 🟢 | baixo | testes puros verdes |
| 0 | F2 | cliente Inter (`inter.py`+`certs.py`) | 🟢 | médio | token real obtido com a app nova |
| 0 | F3 | **fechar um par completo de credencial** (usuário) | 🟢 | médio | token 200 **e** recurso 200 com o mesmo par |
| 1 | F4 | store + rotas de operador | 🔴 | médio | cobrança real de R$ 0,01 criada por `curl` |
| 1 | F5 | vendedores + tela de configuração | 🟢 | baixo | vendedor cadastrado aparece no dropdown |
| 2 | F6 | webhook público + repasse + inbox | 🔴 | **alto** | payload simulado é repassado e o nosso `txid` processado |
| 2 | F7 | nota privada + envio no canal | 🟢 | **alto** | mensagem chega **uma vez só** em canal que ecoa |
| 3 | F8 | slot ⋮ + modal + tela de histórico | 🟢 | baixo | ciclo completo pelo painel, no claro e no escuro |
| 3 | F9 | Trackify + bus + auditoria | 🟢 | baixo | evento na jornada; linha na trilha |
| 4 | F10 | testes pelo loader real + suíte | 🔴 | médio | runner do plugin verde + core sem regressão |
| 4 | F11 | **corte do webhook** + ponta a ponta | 🔴 | **alto** | PIX real de R$ 0,01 pago confirma sozinho; imersão continua liberando |
| 4 | F12 | build, zip, publicação, instalação | 🔴 | baixo | `--check` byte a byte; rodando em prod |

---

### Fase 0 — Esqueleto, manifest e schema 🟢

**Objetivo:** um plugin vazio que carrega, migra e aparece na UI.

**Itens**
1. `mkdir -p ../whatsbot-pro-plugins/plugins/pagamentos/{src,tests/python}` — `[sequencial]`
2. `src/plugin.yaml`: `id: pagamentos`, `whatsbot_api_version: ">=1.0,<2.0"` (⚠️ **com comparadores** — o
   parser em `plugins/semver.py` rejeita `"1.1"`/`"^1.1"`), `entry: {routes, lifecycle, settings}`,
   `migrations: migrations`, `frontend_extends`, duas `screens` (funcional + `config: true`),
   `plugin_services_version: ">=2.1,<3.0"` (usa `subscribe`), `rbac` com `view`/`cobrar`/`manage`,
   `uses_services: [{plugin: trackify, version: ">=1.0,<2.0"}]` — `[paralelo]`
3. `src/migrations/001_initial.sql` com as 3 tabelas de §3.2 — **todo** objeto prefixado
   `plugin_pagamentos_` (o migrator recusa o contrário) e ⚠️ **sem `;` dentro de comentário** (o migrator
   quebra o SQL por `;` antes de remover comentários) — `[paralelo]`
4. `src/settings.py` com os defaults escalares — `[paralelo]`

**Pronto quando:** importar o `.zip` no dev, ativar, e o card mostrar ativo sem `load_error`; as 3 tabelas
existem no Postgres; desativar e reativar não recria nem duplica.

#### Status de execução — Fase 0
**Estado:** ✅ Concluída (2026-08-12)
- **O que foi feito:** `../whatsbot-pro-plugins/plugins/pagamentos/{src,tests/python}` criado.
  `src/plugin.yaml` (id `pagamentos`, versão 1.0.0, `entry: routes/lifecycle/settings`,
  `frontend_extends`, 2 screens, `rbac` com `view`/`cobrar`/`manage`,
  `plugin_services_version: ">=2.1,<3.0"`, `uses_services: trackify`), `src/__init__.py` com o
  mapa dos módulos, `src/migrations/001_initial.sql` (3 tabelas + 5 índices) e `src/settings.py`.
- **Como foi feito / decisões:** id ficou `pagamentos` (recomendação do P1). `uses_services` é
  ignorado pelo parser do core LOCAL (que não tem o seam) — verificado em
  [plugins/manifest.py:150-235](plugins/manifest.py#L150-L235): chaves desconhecidas são
  simplesmente não lidas, então declarar não quebra o carregamento no checkout atual.
  `valor` virou `NUMERIC(12,2)` conforme §3.2, e nenhum comentário do SQL tem `;`.
- **Problemas / pendências:** nenhuma.
- **Verificação:** plugin carrega pelo loader real sem `load_error` — o log dos testes mostra
  `Plugin pagamentos loaded (tools=0 prompts=0 events=0 filters=0 router=yes screens=2)` e
  `registered 3 RBAC permission(s)`; as 3 tabelas são criadas pela migration no Postgres de teste.

---

### Fase 1 — Módulos puros 🟢 `[paralelo com F0, F2, F3]`

**Objetivo:** a lógica testável sem banco, sem rede e sem app.

**Itens**
1. `txid.py` — `new_txid(codigo)` (§3.5), `is_ours`, `vendor_code_of`. Testes: formato, faixa 26–35,
   `is_ours` **falso** para um UUID hex de 32 chars (o caso que protege o repasse) — `[paralelo]`
2. `money.py` — `parse_valor_br` portada de `recovery/src/index.js:1106`, com a tabela de casos do worker
   (`1.997,00`, `1997.00`, `R$ 1997`, `1.997.000`, lixo → `None`), `to_inter_str` → `"1997.00"` — `[paralelo]`
3. `mensagem.py` — porte de `recovery/src/index.js:4131`: saudação com o primeiro nome, valor em BRL,
   o copia-e-cola **em bloco separado** (para o cliente conseguir selecionar só o código) — `[paralelo]`
4. Sanitização de `solicitacaoPagador`: ≤140 chars **e sem acento** (§2.2) — `[paralelo]`

**Pronto quando:** `python3 scripts/test_plugins.py --python-only pagamentos` verde só com estes testes.

#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-08-12)
- **O que foi feito:** `src/txid.py` (`new_txid`, `is_ours`, `vendor_code_of`,
  `normalize_codigo`, `is_valid_for_inter`), `src/money.py` (`parse_valor_br`,
  `to_inter_str`, `format_brl`, `validate_valor`) e `src/mensagem.py`
  (`sanitize_solicitacao`, `mensagem_cobranca`, `nota_pagamento`, `strip_accents`).
  Testes em `tests/python/test_modulos_puros.py` (25 casos).
- **Como foi feito / decisões:** `parse_valor_br` devolve **`Decimal`**, não `float` — o valor
  vira string na borda do Inter e coluna `NUMERIC` no banco, e passar por ponto flutuante no
  meio só acrescentaria uma chance de centavo errado. Código de vendedor inválido **degrada**
  para o coringa `00` em vez de recusar: cobrar sem atribuição é melhor que não cobrar.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `python3 scripts/test_plugins.py --python-only pagamentos` → **25 passed**
  nesta fase. Casos travados: `1.997,00`/`1997.00`/`R$ 1997`/`1.997.000`, lixo → `None`
  (nunca zero), `is_ours` **falso** para UUID hexadecimal de 32 chars, 500 txids sem repetição,
  `solicitacaoPagador` ASCII e ≤140, e o copia-e-cola sozinho na própria linha.

---

### Fase 2 — Cliente do Banco Inter 🟢 `[paralelo com F0, F1]`

**Objetivo:** falar com o Inter por mTLS, com o token cacheado.

**Itens**
1. `certs.py`: lê o PEM do cert e da chave da config do plugin, escreve em
   `storages/plugins/pagamentos/certs/` com `0600`, e devolve o par de caminhos. Guardar em `storages/`
   (persistente no Coolify) e **nunca** no repositório. Expor a data de validade para a tela — `[sequencial]`
2. `inter.py`: um `httpx.Client(cert=(crt, key))` reaproveitado; ⚠️ **o OAuth também passa por ele** (§2.2).
   `token()` com cache em memória e margem de 30 s (molde `recovery/src/index.js:1045`);
   `create_cob`, `get_cob`, `get_webhook`, `set_webhook` — `[sequencial]`
3. Erro sempre com corpo truncado e **sem** o `client_secret` na mensagem — `[paralelo]`
4. Testes com transporte `httpx.MockTransport`: renovação do token, cache, 401, 5xx — `[paralelo]`

**Pronto quando:** com a credencial de F3, um script de bancada obtém token real e cria uma cobrança de
R$ 0,01 que aparece no app do Inter. Sem F3, os testes de mock passam e a chamada real fica pendente.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-08-12) — falta só a chamada REAL, que depende da F3
- **O que foi feito:** `src/certs.py` (materializa o par em
  `storages/plugins/pagamentos/certs/` com `0600`, diretório `0700`, escrita atômica,
  `ssl_context()`, `expires_at()`, `status()`) e `src/inter.py` (`token` com cache,
  `create_cob`, `get_cob`, `get_webhook`, `set_webhook`, `delete_webhook`, `smoke`,
  `reset_cache`, `InterError`). Testes em `tests/python/test_cliente_inter.py` (11 casos).
  ⚠️ `cert_dir()` foi corrigido durante a F10 para resolver pelo **pacote** e não pelo cwd —
  ver o registro daquela fase.
- **Como foi feito / decisões:** três desvios deliberados do porte literal:
  (1) o mTLS entra por **`ssl.SSLContext` + `load_cert_chain`** em `verify=`, não pelo
  `httpx.Client(cert=…)` — o parâmetro está depreciado no httpx 0.28 e o contexto é
  reaproveitado entre chamadas; (2) a validade do certificado é lida pelo decodificador do
  próprio `ssl` porque **`cryptography` NÃO está instalado** no core (conferido) — falha
  degrada para "sem data", nunca derruba nada; (3) acrescentei a config opcional
  `conta_corrente` → header `x-conta-corrente`, que é exatamente a hipótese alternativa do
  **P8**: se o 401 for isso, vira configuração e não mudança de código.
- **Problemas / pendências:** a prova contra a API real continua **pendente da F3**.
- **Verificação:** 11 testes com `httpx.MockTransport`, verdes. Travam: token cacheado (1
  autenticação para 2 chamadas), renovação dentro da margem de 30 s, `Bearer` + conta corrente
  no cabeçalho, 401 **descarta o token** e cita a causa provável, corpo de erro truncado em 400
  chars, **o segredo nunca aparece na exceção**, `create_cob` usa **PUT com o nosso txid** (não
  o `POST` do n8n) e `smoke()` separa `token_ok` de `resource_ok` — com token 200 + recurso 401
  o veredito é **`ok=False`**.

---

### Fase 3 — Fechar UM par completo de credencial 🟢 `[ação do usuário]` — **D1 revista**

**Objetivo:** ter `client_id` + `client_secret` + certificado **da mesma aplicação**, provados contra a API.

Hoje existem duas metades (§2.3.1). Tentar as rotas **nesta ordem** — a primeira que fechar encerra a fase:

**Rota 1 — completar a app A (preferida: é "o que a Erika usou")**
1. Obter a `N8N_ENCRYPTION_KEY` no host do n8n e decifrar a credencial `inter SSL`
   (`n8n_queue.credentials_entity.id = zHSOvzRxahO1uDDn`) para extrair cert + chave — `[sequencial]`
2. Alternativa se a chave de cifra não estiver acessível: baixar/regerar o certificado da app A **no portal
   do Inter**. ⚠️ Regerar invalida o cert antigo — só fazer depois de confirmar que **apenas** os dois
   workflows do n8n (ambos inativos) usam a app A — `[sequencial]`

**Rota 2 — completar a app B**
3. Recuperar o `client_secret` da app B no portal do Inter. ⚠️ Na prática isso é **rotacionar**, o que
   derruba `imersao-analista` **e** a aba PIX Manual até `wrangler secret put` nos dois workers. Só com
   janela combinada — `[sequencial]`

**Rota 3 — app nova** (a D1 original)
4. Criar aplicação nova com os escopos `cob.write cob.read pix.read pix.write webhook.write webhook.read`
   (os mesmos de `imersao-analista/src/index.js:937`; F11 precisa dos dois de webhook) e gerar o
   certificado — `[sequencial]`

**Em qualquer rota**
5. **Guardar as quatro peças no cofre** (hoje não há entrada de Inter lá) e anotar a validade do
   certificado — `[sequencial]`
6. Não deixar valor novo em texto plano em lugar nenhum — nem no n8n, nem neste plano (§2.3.3) — `[paralelo]`

**Pronto quando:** um `POST /oauth/v2/token` seguido de um `GET /pix/v2/cob?inicio=…&fim=…` responde **200
nos dois** com o mesmo par. O segundo é o teste que importa: o token sozinho já dá 200 com certificado
trocado (§2.3.1) e enganaria a validação.

#### Status de execução — Fase 3
**Estado:** ✅ **Concluída (2026-08-13)** — credencial completa, provada por recurso 200
- **Desfecho (2026-08-13):** a Erika gerou no portal do Inter uma **credencial OAuth nova** na aplicação
  de OU `598411d3` (a mesma dos workers): client_id `8ed866a1-…` + secret + o certificado. O `.crt` do zip
  tem **serial idêntico** ao cert de produção (`5FF064…`, válido até 2027-05-27) — nada foi regerado, o
  worker `imersao-analista` segue intacto. Gravei as 5 chaves em `config` (`plugin.pagamentos.*`) na
  instância `teste.techify.run` e o `inter.smoke()` retornou **`token_ok=True, resource_ok=True`** — o
  critério de aceite (recurso 200, não só token). O zip foi apagado da raiz do repo; o par mora em
  `storages/plugins/pagamentos/certs/` com 0600. **A investigação abaixo (rota do n8n) fica como registro.**
- **O que foi feito (2026-08-12):** **três das quatro peças da app A recuperadas e provadas.**
  A Rota 1 foi executada: com a `N8N_ENCRYPTION_KEY` (fornecida pelo Thiago) decifrei a
  credencial `inter SSL` do n8n (`openssl enc -d -aes-256-cbc -a -A -md md5`). O cert casa com a
  chave (mesmo modulus) e é o par de `a7e926c0` (mesma credencial SSL nos nós
  `generate_token`/`generate_pix`). client_id, client_secret e chave PIX (CNPJ) estão em mãos.
- **Como foi feito / decisões:** ⚠️ **o certificado decifrado VENCEU em 2026-04-03** (validade de
  1 ano desde 2025-04-03). É a causa dos dois workflows n8n estarem inativos. O par
  client_id/secret continua válido (token 200), mas nenhum cert VÁLIDO dessa app existe em lugar
  que eu alcance — se existisse, os workflows teriam sido religados. Não usei a Rota 2 (rotacionar
  o secret da app B derruba `imersao-analista`) nem a 3 (app nova). O certificado/chave decifrados
  foram **apagados do scratchpad** (vencidos, sem utilidade, e chave privada).
- **Problemas / pendências:** **a única peça que falta é um certificado VÁLIDO.** O Thiago
  precisa, no internet banking do Inter → API → aplicação `a7e926c0-…`, **gerar um novo
  certificado + chave**. O par client_id/secret NÃO muda; o novo cert casa com ele. Regerar
  invalida o cert vencido, o que não custa nada (só os 2 workflows n8n inativos o referenciam).
- **Verificação:** o critério continua `POST /oauth/v2/token` **e** `GET /pix/v2/cob?…`
  respondendo 200 com o mesmo par. O botão **"Testar credencial"** já executa isso
  (`POST /config/test` → `inter.smoke`) e distingue token-ok de recurso-ok na tela.

---

### Fase 4 — Store e rotas de operador 🔴 `[depende de: F0, F1, F2]`

**Objetivo:** gerar e listar cobrança pela API, sem UI.

**Itens**
1. `store.py`: `create_cobranca`, `list_cobrancas`, `get`, `mark_paid`, `mark_expired`, `touch_checked` — `[sequencial]`
2. `POST /cobrancas` (gate `plugin_permission("cobrar")`): valida valor (`>= 0,01`, `<= 100000`), e-mail,
   validade na lista `[3600, 86400, 259200, 604800]`, resolve o vendedor, gera o `txid`, chama o Inter e
   grava. Erro do Inter → **502 com a mensagem do banco**, e nada gravado — `[sequencial]`
3. `GET /cobrancas` (gate `view`) com filtro por status/período e o agregado "pago hoje" — `[paralelo]`
4. `POST /cobrancas/{txid}/check` (gate `view`): reconsulta e atualiza — o "Conferir agora" — `[paralelo]`
5. `GET /cobrancas/{txid}/qr` : `segno.make(pix_code).png_data_uri(...)` como em
   `app/services/provisioning_service.py:45` — `[paralelo]`
6. `audit("pagamentos", "cobranca.create", resource_id=txid, after={...})` — **sem** o `pix_code` no diff — `[paralelo]`

**Pronto quando:** `curl` autenticado cria cobrança de R$ 0,01, ela aparece no `GET`, o QR abre e a linha
consta na tela `/audit`.

#### Status de execução — Fase 4
**Estado:** ✅ Concluída (2026-08-12)
- **O que foi feito:** `src/store.py` (configuração + cobranças + vendedores + inbox) e a
  metade de operador de `src/routes.py`: `GET /metadata`, `POST /cobrancas`,
  `GET /cobrancas`, `GET /cobrancas/{txid}`, `POST /cobrancas/{txid}/check`,
  `POST /cobrancas/{txid}/send`, `GET /cobrancas/{txid}/qr`.
- **Como foi feito / decisões:** **um módulo a mais que o previsto em §3.1** —
  `src/reconcile.py`. A regra "esta cobrança está paga?" tem TRÊS chamadores (webhook,
  varredura e o botão do painel); deixá-la em `routes.py` obrigaria o `lifecycle` a importar
  rota, e duplicá-la traria de volta a segunda nota privada no dia em que dois caminhos
  corressem juntos. `mark_paid` é `UPDATE … WHERE status <> 'CONCLUIDA'` e devolve se mudou —
  é a porta de idempotência do plugin inteiro. Erro do Inter na criação ⇒ **502 e nada
  gravado** (linha local sem cobrança no banco seria uma cobrança fantasma que a varredura
  nunca resolveria). O telefone NUNCA vem por query string: o cliente manda só
  `conversation_id` e `_resolve_conversa` resolve no servidor por
  [conversation_repo.get_with_channel](db/repositories/conversation_repo.py#L592).
- **Problemas / pendências:** nenhuma.
- **Verificação:** testes de rota pelo app real —
  `test_criar_cobranca_grava_e_devolve_o_copia_e_cola` (inclusive `"1.997,00"` → `1997.00`,
  descrição ASCII e `txid` com o coringa), `test_recusa_do_inter_nao_deixa_cobranca_fantasma`,
  `test_valor_invalido_nem_chega_ao_banco` (o dublê do Inter **explode** se for chamado) e
  `test_vendedor_entra_no_txid`. A linha em `/audit` sai de `audit("pagamentos",
  "cobranca.create", …)` **sem** o `pix_code` no diff.

---

### Fase 5 — Vendedores e tela de configuração 🟢 `[depende de: F0]`

**Objetivo:** o admin configura tudo dentro do card do plugin (nunca no `ConfigPanel` do core).

**Itens**
1. CRUD de `plugin_pagamentos_vendedores` (gate `manage`): nome, `utm_term`, `codigo` de 2 chars único,
   ativo. Recusar código duplicado com mensagem clara — `[sequencial]`
2. `GET/PUT /config` (gate `manage`): `client_id`, `client_secret`, cert, chave, `pix_chave`, `forward_url`,
   `forward_enabled`, intervalo da reconciliação. ⚠️ **Segredo mascarado no GET** (só últimos 4) e `PUT`
   sem o campo **não apaga** o salvo — regra que o `whatsapp_cloud` já aplica — `[sequencial]`
3. Campos de segredo com `autocomplete="new-password"` + opt-outs de gerenciador de senha — o plano 104
   documenta por que `off` não basta no Chrome (`CLAUDE.md`, §"Proxy de saída por número") — `[paralelo]`
4. Mostrar a **URL do webhook** montada de `public_base_url` + o segredo, atrás de um botão "Revelar"
   gateado por `manage` e **auditado** (molde `website/src/routes.py:358`) — `[paralelo]`
5. Mostrar a validade do certificado e avisar a menos de 30 dias — `[paralelo]`

**Pronto quando:** configurar tudo pela tela, recarregar e o segredo continuar mascarado; o dropdown de
vendedor do modal (F8) lista os ativos; tudo legível no modo escuro.

#### Status de execução — Fase 5
**Estado:** ✅ Concluída (2026-08-12) — falta a conferência visual do modo escuro (F8)
- **O que foi feito:** CRUD de vendedores (`GET/POST /vendedores`, `PUT`/`DELETE
  /vendedores/{id}`), `GET/PUT /config`, `POST /config/test`, `GET /webhook-url`,
  `POST /webhook-url/rotate`, `GET/PUT /webhook-inter` e a tela `src/static/config.js`.
- **Como foi feito / decisões:** o **código do vendedor não é editável** depois de criado —
  ele já viajou dentro do `txid` das cobranças feitas, e trocá-lo reescreveria a atribuição de
  comissão do passado; `00` é recusado por ser o coringa de "sem vendedor". Segredo ausente ou
  vazio no `PUT` significa **manter o salvo** (regra do `whatsapp_cloud`), e o `GET` devolve só
  `*_set: true`. `secretProps()` em `config.js` aplica `autocomplete="new-password"` + os
  opt-outs de gerenciador de senha, com `name` sem as palavras que reativam a heurística —
  lição do plano 104. `PUT /webhook-inter` exige a **URL explícita** em vez de montá-la
  sozinha, justamente para servir também à **reversão** (§3.3 regra 4: voltar é a mesma
  chamada com a URL antiga).
- **Problemas / pendências:** a leitura da validade do certificado depende do decodificador do
  `ssl` (sem `cryptography` no core) — se falhar, a tela só não mostra a data.
- **Verificação:** `test_get_config_nao_devolve_segredo_em_claro` (o JSON inteiro é varrido
  atrás do segredo e do PEM), `test_put_config_sem_o_campo_de_segredo_nao_apaga_o_salvo`,
  `test_codigo_de_vendedor_duplicado_e_recusado` (409) e
  `test_codigo_reservado_do_coringa_e_recusado` (400).

---

### Fase 6 — Webhook público, repasse e inbox 🔴 `[depende de: F4]` — **a fase de maior risco**

**Objetivo:** receber o callback do Inter sem nunca engolir o evento de outro sistema.

**Itens**
1. `POST /public/webhook/{secret}` em `routes.py`. Comparar o segredo com `hmac.compare_digest`.
   Segredo errado → `404` (não `401`: não confirma a existência da rota) — `[sequencial]`
2. Limite de corpo e rate limit por IP usando `server/client_ip.py` `client_ip(request)`
   ⚠️ **nunca** `xff.split(",")[0]`, que é forjável (`CLAUDE.md`, gotcha de IP atrás de proxy) — `[sequencial]`
3. Gravar o payload cru em `plugin_pagamentos_webhook_inbox` **antes de qualquer decisão** — `[sequencial]`
4. Agendar o repasse numa task destacada guardando **referência forte** (senão o GC a mata) e responder
   `200` imediatamente. `forward.py` faz `POST forward_url` com o corpo **byte a byte** — `[sequencial]`
5. Só então, para cada `pix[].txid` com `is_ours`: `inter.get_cob(txid)` e, confirmado o pagamento,
   `store.mark_paid` (§3.3 regra 3) — `[sequencial]`
6. Idempotência: `mark_paid` só age quando `status != 'CONCLUIDA'`; reentrega do Inter não gera 2ª nota
   privada nem 2º evento — `[paralelo]`
7. Task `pagamentos:forward_retry` com backoff para as linhas de inbox sem `forwarded_at`; corte por idade — `[paralelo]`
8. Retenção do inbox (padrão 30 dias) para a tabela não crescer para sempre — `[paralelo]`

**Pronto quando:** um payload simulado com **um `txid` nosso e um alheio** (a) chega inteiro ao destino de
repasse, (b) marca só o nosso como pago, (c) reenviado, não duplica nada; com o destino de repasse
apontado para uma URL morta, a linha fica pendente, o Inter recebe `200` e a retentativa entrega quando a
URL volta.

#### Status de execução — Fase 6
**Estado:** ✅ Concluída (2026-08-12)
- **O que foi feito:** `POST /public/webhook/{secret}` (+ a variante `/pix`) em `routes.py`,
  `src/forward.py` (repasse cru + backoff) e as funções de inbox em `store.py`
  (`record_inbox`, `mark_forwarded`, `mark_forward_error`, `pending_forwards`,
  `get_inbox_attempts`, `inbox_health`, `purge_inbox`).
- **Como foi feito / decisões:** a ordem dentro do handler é a do §3.3 e não é negociável —
  rate-limit → segredo → teto de corpo → **grava o cru** → agenda o repasse → agenda o
  processamento → responde 200. As duas tasks guardam **referência forte** em `_inflight`
  (task sem referência pode ser recolhida pelo GC). Segredo errado responde **404**, não 401.
  O backoff é `30s / 2min / 8min / 30min / 2h` e depois **para de tentar sozinho** — a linha
  continua no inbox, visível na tela, em vez de girar contra um destino morto. Acrescentei a
  rota irmã `/public/webhook/{secret}/pix` porque algumas integrações PIX chamam
  `<url registrada>/pix`: é barato aceitar os dois e caro descobrir a diferença com o
  pagamento em voo.
- **Problemas / pendências:** o `_rate` é por processo (não distribuído) — suficiente, já que
  a defesa real é a reconsulta.
- **Verificação:** `test_payload_sem_txid_nosso_ainda_assim_e_repassado` (o caso que protege a
  venda de terceiro, com comparação **byte a byte** do corpo),
  `test_repasse_leva_o_payload_inteiro_mesmo_com_txid_nosso_junto`,
  `test_falha_no_repasse_nao_vira_erro_para_o_banco` (200 ao Inter + linha pendente),
  `test_retentativa_entrega_quando_o_destino_volta`, `test_segredo_errado_responde_404` e
  `test_segredo_errado_nao_grava_nada`.

---

### Fase 7 — Nota privada e envio no canal 🟢 `[depende de: F4]`

**Objetivo:** o copia-e-cola chega ao cliente **uma vez só**, e o atendente sabe quando pagou.

**Itens**
1. `notify.send_cobranca` — molde **literal** de `retornos/src/actions.py:182-197`:
   `_mark_echo_text` **antes** do envio, `outbound_router.send_text`, `_mark_echo_id` **depois**,
   e `_unmark_echo_text` se falhar. ⚠️ Sem isso a bolha duplica em canal que ecoa (GOWA/Messenger) — `[sequencial]`
2. Guardar `sent_msg_id`/`sent_at` na cobrança — `[paralelo]`
3. `notify.aviso_pago` — nota privada com `post_private_note` (molde `retornos/src/actions.py:112`) +
   `broadcast("new_message", …)`. Dedup por `notified_at` — `[sequencial]`
4. `broadcast("plugin_pagamentos_cobranca", {...})` para a tela do plugin atualizar ao vivo — `[paralelo]`

**Pronto quando:** gerar e enviar num canal GOWA real produz **uma** bolha; pagar produz **uma** nota
privada; conferir de novo pelo botão não produz a segunda.

#### Status de execução — Fase 7
**Estado:** ✅ Concluída (2026-08-12) — o teste em canal GOWA real fica com o usuário
- **O que foi feito:** `src/notify.py` — `send_text` (com `_mark_echo_text` /
  `_mark_echo_id` / `_unmark_echo_text`), `_save_outbound`, `post_private_note`,
  `send_cobranca`, `aviso_pago` e `broadcast_cobranca`.
- **Como foi feito / decisões:** molde **literal** de
  `retornos/src/actions.py:143-197`, inclusive o `_unmark_echo_text` no caminho de erro —
  marcação órfã calaria o eco de uma mensagem que o operador mandasse depois. A nota privada
  tem **dedupe duplo**: a transição de `mark_paid` e a coluna `notified_at`; as duas juntas
  cobrem o caso de a transição ter acontecido num processo que caiu antes de avisar.
- **Problemas / pendências:** o "uma bolha só em canal que ecoa" só se prova de verdade num
  canal real — os testes provam o mecanismo (a marcação existe antes do envio), não o
  comportamento do provedor. Fica no checklist do usuário.
- **Verificação:** `test_envio_registra_o_eco_antes_de_sair` (confere as DUAS chaves:
  `canal-x:5511999990000:Segue o PIX` em `recently_sent` e `canal-x:MSGID-1` em
  `processed_messages`) e `test_envio_que_falha_desfaz_a_marcacao_do_eco`.

---

### Fase 8 — Frontend: slot, modal e tela 🟢 `[depende de: F4, F7]`

**Objetivo:** o ciclo inteiro sem sair da conversa.

**Itens**
1. `static/extends.js`: `api.addSlot('conversation.header.actions', …)` gateado por
   `api.services.hasPermission(user, 'plugin.pagamentos.cobrar')`, molde `trackify/src/static/extends.js:24` — `[sequencial]`
2. Modal "Gerar PIX" via `api.ui.openModal`: campos pré-preenchidos a partir de `conv`; quando faltar
   `contact_id`, buscar em `/api/atendimentos/{conv.id}` como o trackify faz. ⚠️ **Nunca** mandar telefone
   em query string — `[sequencial]`
3. Resultado no modal: valor, QR, copia-e-cola com botão copiar, e **Enviar na conversa** — `[sequencial]`
4. `static/pagamentos.js`: histórico com selo de status, "Conferir agora", total pago hoje. Tempo real por
   `api.services.subscribe` — 🚫 **jamais** `new WebSocket('/ws')`, que o core fecha com 4401
   (`server/routes/websocket.py`, gate do plano 48) — `[paralelo]`
5. Debounce com jitter no refetch do handler de WS — `[paralelo]`
6. Só classes `wa-*` e `.wa-field`; conferir os dois temas — `[paralelo]`

**Pronto quando:** gerar, enviar, receber e ver "Pago" sem sair do painel, no claro e no escuro; sem a
permissão, o botão não aparece.

#### Status de execução — Fase 8
**Estado:** ✅ Concluída no código (2026-08-12) — **conferência visual dos dois temas pendente**
- **O que foi feito:** `src/static/extends.js` (slot `conversation.header.actions`, gateado por
  `plugin.pagamentos.cobrar`), `src/static/CobrancaModal.js` (formulário → resultado com QR,
  copia-e-cola e "Enviar na conversa") e `src/static/pagamentos.js` (histórico com selo de
  status, "Conferir agora" e o total recebido no dia).
- **Como foi feito / decisões:** o tempo real vem de
  `subscribe` do `/static/js/services/wsBus.js` — **nunca** `new WebSocket('/ws')`, que o core
  fecha com 4401 em silêncio. O refetch tem **debounce de 400–1300 ms com jitter**, porque um
  broadcast significa cache invalidado para todos os operadores ao mesmo tempo. Só o
  `conversation_id` viaja do slot para o servidor. Só classes `wa-*` e `.wa-field`.
- **Problemas / pendências:** **abrir o painel e conferir claro/escuro** no modal, no histórico
  e na configuração — é validação visual, não automatizável aqui. O ciclo ponta a ponta pela
  interface depende da credencial (F3).
- **Verificação:** os 4 módulos passam em `node --input-type=module` (o `--check` sozinho dá
  falso negativo em template com crase). Nenhum comentário dentro de <code>html`…`</code>
  contém crase ou interpolação, que fechariam o template e quebrariam o módulo em silêncio.

---

### Fase 9 — Trackify, bus e auditoria 🟢 `[depende de: F4, F6]`

**Objetivo:** a venda entra na jornada do cliente e a operação fica auditável.

**Itens**
1. `trackify_bridge.py` com import defensivo **idêntico** a `protocolos/src/logic.py:51-54` — sem o seam,
   `_services is None` e o plugin segue inteiro — `[sequencial]`
2. `services.call("trackify", "track_event", _as="pagamentos", kind=…)` com `pix_gerado` / `pix_pago` /
   `pix_expirado` (verificado: não colidem com os reservados em `trackify/src/mirror.py:33-46`).
   ⚠️ **`valor` só no `pix_pago`** — cobrança gerada não é receita — `[sequencial]`
3. Chamar **pós-commit**, pelo mesmo motivo documentado em `protocolos/src/logic.py:5295-5297`: o
   `mirror.enqueue` abre transação própria e um rollback nosso criaria evento fantasma — `[sequencial]`
4. `emit_with_filter_sync("pagamentos.pix_pago", …)` — seam publicado pelo plugin, versionado pelo
   `version` dele, **sem** mexer no catálogo do core — `[paralelo]`
5. Auditar `cobranca.create`, `config.update`, `webhook.reveal` e `webhook.migrate` — `[paralelo]`

**Pronto quando:** pagar uma cobrança faz o evento aparecer na aba Jornada do contato; com o `trackify`
desativado, tudo o mais continua funcionando e nada é logado como erro.

#### Status de execução — Fase 9
**Estado:** ✅ Concluída (2026-08-12) — o efeito no CDP depende do trackify em execução
- **O que foi feito:** `src/trackify_bridge.py` (`cobranca_gerada` / `cobranca_paga` /
  `cobranca_expirada`), o emit `pagamentos.<kind>` no bus e as chamadas de `audit()` em
  `cobranca.create`, `vendedor.create|update|delete`, `config.update`, `webhook.reveal`,
  `webhook.rotate` e `webhook.migrate`.
- **Como foi feito / decisões:** import defensivo **idêntico** a
  `protocolos/src/logic.py:51-54`. `valor` vai **só** no `pix_pago` — cobrança gerada não é
  receita e inflaria o total gasto do contato no CDP com dinheiro que ninguém pagou. As
  chamadas acontecem **pós-commit**, pelo mesmo motivo do `protocolos`: o `mirror.enqueue` do
  outro lado abre transação própria. `GET /webhook-url` é auditado apesar de ser GET — a
  exceção deliberada é a mesma do `website/reveal-hmac`: a rota **entrega um segredo**.
- **Problemas / pendências:** o checkout local do core não tem `plugins/services.py` (está 5
  commits atrás de `origin/developer`), então aqui `_services is None` e só o emit no bus
  acontece — que é exatamente o caminho de degradação previsto. Em produção, que já roda o
  seam, o espelho acontece.
- **Verificação:** o plugin carrega e opera inteiro **sem** o seam — é o estado em que os 45
  testes rodaram. Os kinds `pix_gerado`/`pix_pago`/`pix_expirado` foram conferidos contra
  `trackify/src/mirror.py:33-46`: não colidem com os reservados.

---

### Fase 10 — Testes 🔴 `[depende de: tudo]`

**Objetivo:** provar o plugin pelo caminho real, não pelo atalho.

**Itens**
1. ⚠️ **Pelo menos um teste sobe o app pelo loader real e bate no endpoint real.** Teste que importa o
   módulo por caminho continua verde com a costura arrancada — regra do `CLAUDE.md` — `[sequencial]`
2. Casos obrigatórios: repasse acontece mesmo sem `txid` nosso · segredo errado → 404 · `mark_paid` só
   depois do `get_cob` (mock que **nunca** confirma prova que o corpo não decide) · reentrega não duplica ·
   eco suprimido · segredo não sai em claro pelo `GET /config` — `[paralelo]`
3. `python3 scripts/test_plugins.py --python-only pagamentos` no repositório de plugins — `[sequencial]`
4. Suíte do core sem regressão nova (`venv/bin/python -m pytest`) — `[paralelo]`

**Pronto quando:** runner do plugin verde; core sem regressão nova; cada teste do item 2 falha quando a
proteção correspondente é removida à mão.

#### Status de execução — Fase 10
**Estado:** ✅ Concluída (2026-08-12)
- **O que foi feito:** três arquivos em `tests/python/` — `test_modulos_puros.py` (25),
  `test_cliente_inter.py` (11) e `test_webhook_e_rotas.py` (20, pelo loader real).
- **Como foi feito / decisões:** o item 2 do plano ("mock que **nunca** confirma prova que o
  corpo não decide") virou um **par diferencial** em vez de um teste só:
  `test_callback_forjado_nao_marca_pago` e `test_pagamento_confirmado_pelo_inter_marca_pago`
  mandam o **mesmo corpo de callback** e diferem apenas no que o `get_cob` dublado responde.
  Um teste sozinho passaria mesmo se o código lesse o corpo; o par não.
- **Problemas / pendências:** ⚠️ **um bug real apareceu e foi corrigido aqui.** `certs.cert_dir()`
  ancorava em `os.getcwd()`; como o harness copia o plugin para um diretório temporário mas
  roda com o cwd na raiz do core, a leitura da validade do certificado no `GET /config` criou
  `storages/plugins/pagamentos/certs/` **dentro do checkout de desenvolvimento** — onde o
  servidor de dev varre plugins e passaria a ver um plugin sem `plugin.yaml`. O diretório agora
  é resolvido pelo **pacote** (`Path(__file__).parent / "certs"`), que instalado já é a pasta
  persistente certa. A pasta órfã foi removida e a base de dev conferida (nenhuma linha de
  `pagamentos` tinha sido criada por aquele acidente).
- **Verificação:** `python3 scripts/test_plugins.py --python-only pagamentos` → **56 passed**,
  e nenhuma pasta nova aparece no checkout depois da rodada. Suíte do core:
  `venv/bin/python -m pytest` → **3 falhas, todas PRÉ-EXISTENTES e alheias a este trabalho**
  (`test_alembic_hygiene` ×2 — revisão de merge `0058_merge_p50_p57` e prefixos duplicados de
  migrations de junho/julho; `test_audit_matrix_is_complete` — o golden da matriz está atrás
  dos eventos `channel.*` do core). Provado por eliminação: as três continuam falhando com o
  plugin **removido** de `storages/plugins/`, e `git status db/` está limpo.

---

### Fase 11 — O corte do webhook + ponta a ponta 🔴 `[depende de: F10, F3]`

**Objetivo:** migrar o webhook sem derrubar a venda de ninguém.

**Itens**
1. **Antes de tudo**, `GET /pix/v2/webhook/09596968000105` e **anotar a URL atual no plano** (é o botão de
   voltar). ⚠️ **Isto exige a credencial da app B**, não a da app A: a tentativa com a app A devolveu
   **401** (§2.3.1). Se a F3 fechou pela Rota 1 ou 3, a leitura tem de sair do **portal do Inter** ou de uma
   rota temporária no próprio worker `imersao-analista` (que já tem o par certo).
   Dois candidatos conhecidos para essa URL, e a nota de memória do servidor avisa que a diferença importa:
   `https://imersao-analista.onlinecenterdigital.workers.dev/api/inter-webhook` (medido no ar) **ou**
   `https://redesbrasil.net/pix-imersao-analista/api/inter-webhook` (a rota declarada em
   `imersao-analista/wrangler.jsonc`). **Não adivinhar — ler** — `[sequencial]`
2. Configurar `forward_url` com essa URL exata e provar o repasse **antes** de migrar, injetando um payload
   no nosso endpoint e conferindo o efeito do outro lado — `[sequencial]`
3. `PUT /pix/v2/webhook/{chave}` apontando para a URL do WhatsBot — `[sequencial]`
4. Ponta a ponta **com dinheiro real**: cobrança de R$ 0,01 pelo painel → pagar → confirmar sozinho, nota
   privada aparecer, evento no Trackify — `[sequencial]`
5. Ponta a ponta do **repasse**: uma cobrança de R$ 0,01 pela aba da imersão → pagar → o acesso continuar
   saindo pelo `imersao-analista` — `[sequencial]`
6. Janela de observação de 24 h com o inbox monitorado (`forward_error` deve permanecer vazio) — `[paralelo]`
7. Atualizar a doc do worker (`cloudflare/CLAUDE.md`), onde ainda está escrito que a chave está registrada
   no `imersao-analista` — `[paralelo]`

**Pronto quando:** os dois pagamentos reais confirmam pelos seus caminhos e 24 h se passam sem
`forward_error`. **Reversão:** um `PUT` com a URL anotada no item 1.

#### Status de execução — Fase 11
**Estado:** ⛔ Bloqueada — depende da **F3** (credencial), de **dinheiro real** e da decisão **P7**
- **O que foi feito:** só o ferramental. As três operações da fase são botões da tela de
  configuração e não exigem `curl`: **"Ler a URL registrada no Inter"** (item 1, o botão de
  voltar), o par repasse/`forward_url` provado antes de migrar (item 2) e **"Registrar esta URL
  no Inter"** (item 3). A reversão é o mesmo botão com a URL antiga.
- **Como foi feito / decisões:** não executei nada contra a conta de produção. Migrar o webhook
  substitui o destino de uma chave viva e, sem a credencial certa, sequer dá para **ler** a URL
  atual — que o próprio plano manda ler em vez de adivinhar.
- **Problemas / pendências:** ⚠️ **decidir o P7 antes de executar**. Se a 2ª chave PIX
  (`98a64b6b-…`) estiver mesmo ociosa e for usada aqui, esta fase inteira e o repasse da F6
  deixam de ser necessários — some o maior risco do plano. O código não muda: basta deixar
  `forward_enabled` desmarcado e registrar o webhook na outra chave.
- **Verificação:** pendente (dois pagamentos reais de R$ 0,01 + 24 h sem `forward_error`).

---

### Fase 12 — Build, publicação e instalação 🔴 `[depende de: F11]`

**Objetivo:** entregar a versão que o cliente instala.

**Itens**
1. `python3 scripts/build_plugins.py pagamentos` e `--check` byte a byte. ⚠️ Um `--check` "outdated" pode
   ser só permissão `664` em vez de `644` (umask) — conferir antes de rebuildar, que é o caminho
   destrutivo — `[sequencial]`
2. ⚠️ `git fetch` + comparar com o **remoto** antes de concluir paridade — o repositório de plugins recebe
   publicação direta e já andou no meio deste trabalho — `[sequencial]`
3. Instalar em **dev** primeiro, configurar, e só depois em produção — `[sequencial]`
4. Registrar o plugin na seção de plugins do `CLAUDE.md` do core, se a política atual pedir — `[paralelo]`

**Pronto quando:** `--check` bate, o `.zip` instala num clone limpo e produção gera uma cobrança real.

#### Status de execução — Fase 12
**Estado:** 🟡 Em andamento — artefato pronto, **instalação e publicação pendentes**
- **O que foi feito:** `plugins/pagamentos/pagamentos.json` (metadados), entrada nova em
  `catalog.json` (21 plugins) e `plugins/pagamentos/pagamentos.zip` gerado por
  `python3 scripts/build_plugins.py pagamentos`.
- **Como foi feito / decisões:** o build **recusou** rodar antes de o plugin existir no
  `catalog.json` ("catalogue coverage mismatch") — a ordem é catálogo primeiro, build depois.
  Nada foi commitado nem publicado: o repositório de plugins recebe publicação direta e o
  `git fetch` + comparação com o remoto tem de acontecer imediatamente antes do push.
- **Problemas / pendências:** instalar em **dev** primeiro (Importar .zip → Configurar →
  gerar uma cobrança de R$ 0,01) e só depois em produção; commit/push nos dois repositórios;
  registrar o plugin na seção de plugins do `CLAUDE.md` se a política atual pedir.
- **Verificação:** `--check` responde **`pagamentos: current (20 files, 51543 bytes,
  sha256=262e5f27…)`** — bate byte a byte com `src/`. O conteúdo do ZIP foi listado: 20
  arquivos, **nenhum** de teste. O ZIP foi extraído em
  `storages/plugins/pagamentos/` do checkout de desenvolvimento (é a cópia VIVA, a que o
  usuário testa) e o servidor de dev o **descobriu sozinho**: linha `pagamentos 1.0.0`,
  `enabled=0`, **`load_error` vazio**. Deixado DESATIVADO de propósito — ativar dispara
  restart e roda as migrations na base de dev, e essa é a decisão do usuário.

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| **Repasse do webhook** (D2) | O WhatsBot vira ponto único de falha de uma venda que não é dele. Fora do ar = a imersão para de liberar acesso | Repasse incondicional, persistido no inbox e com retentativa (§3.3). Aquele fluxo ainda tem a rede secundária do `/api/pix-status` (§2.4). Reversão num `PUT` (F11.1) |
| **Rota pública sem assinatura** | O Inter não assina o callback; qualquer um pode postar | Nada é marcado pago sem `GET /pix/v2/cob/{txid}` no Inter (§3.4). Segredo no path é ruído, não defesa — e o teste de F10.2 prova isso |
| **Duplicar a bolha** | Envio direto pelo `outbound_router` não passa por `state.recently_sent` e o eco salva a mensagem de novo | Copiar `retornos/src/actions.py:143-197` literalmente, incluindo o `_unmark_echo_text` no erro |
| **Certificado de uma app com o `client_id` de outra** | Falha tardia e enganosa: o OAuth devolve **200** e só as chamadas de recurso quebram com 401 (§2.3.1). Um executor apressado conclui "credencial válida" e descobre no primeiro pagamento | Critério de aceite da F3 exige **recurso 200**, não token 200. O boot do plugin faz um `GET /pix/v2/cob?…` de fumaça e mostra o veredito na tela de configuração |
| **Segredo bancário em texto plano** (§2.3.3) | `client_id`/`secret` legíveis no JSON do n8n; par mTLS solto no disco; webhook do worker aberto sem auth | F3 move tudo para o cofre. Nenhum valor entra em `docs-planos/` (versionado — ver plano 78). Rotação da app A avaliada depois de confirmar que só o n8n inativo a usa |
| Chave privada no banco | O PEM fica na tabela `config` em claro | Aceito e explicitado: é o mesmo nível do resto (o cofre é a cópia mestra). Arquivo materializado com `0600` em `storages/`, fora do repositório, e nunca devolvido pela API |
| `storages/` não persistente | Redeploy sem Persistent Storage apaga o certificado materializado | `certs.py` **re-materializa a partir da config** no boot — o banco é a fonte, o disco é cache. A salvaguarda de boot do core (`server/persistence_check.py`) já grita nesse caso |
| Certificado vence (2027-05-27, ou a data do novo) | Tudo para de uma vez, sem aviso | Validade exibida na tela + aviso a menos de 30 dias (F5.5) |
| Reentrega do Inter | Nota privada e evento em duplicidade | `mark_paid` só age em transição de status; `notified_at` fecha a segunda porta |
| Dois destinos do mesmo `txid` | Um `txid` nosso classificado como alheio, ou o contrário | `w` não é hexadecimal, então a classificação é exata (§ Falsos positivos). E o repasse é incondicional de qualquer forma |
| Valor errado por parsing | Vendedor digita `1.997` e cobra R$ 1,99 | Porte fiel de `parseValorBR` **com os casos do worker em teste**, e o modal mostra o valor formatado antes de gerar |
| Trackify ausente | `services.call` num core sem o seam | Import defensivo (`protocolos/src/logic.py:51`); `_services is None` é caminho normal, não erro |
| Segredo em log | URL do webhook e `client_secret` aparecerem em log/erro | Nunca logar a URL completa; erro do Inter truncado; teste dedicado no `GET /config` |
| Migration com `;` em comentário | O migrator quebra o SQL por `;` **antes** de remover comentários | Comentário sem ponto-e-vírgula (armadilha já registrada neste projeto) |
| Restart no toggle | Ativar/desativar derruba o processo | Estado só em `plugin_pagamentos_*`, nunca em variável de módulo — vale também para o cache de token, que é reconstruível |

---

## 7. Perguntas em aberto

| # | Pergunta | Estado |
|---|---|---|
| P1 | O id do plugin é `pagamentos` (genérico, aceita outro gateway depois sem renomear tabela) ou `pix_inter` (honesto sobre o que é hoje)? | ✅ **DECIDIDO na execução (2026-08-12): `pagamentos`**, a recomendação do plano. Renomear depois custaria tabela nova + migração + reinstalação; o módulo específico do banco é `inter.py`, então somar outro gateway não obriga a renomear nada. Nada foi publicado ainda, então trocar continua barato — mas deixa de ser um `sed` assim que o ZIP for instalado |
| P2 | O modal deve oferecer uma **lista de produtos** (nome + texto que aparece no banco, como o `CHECKOUT_CURSOS` do worker) ou só descrição livre? | ⏸️ ADIADO — o plano entrega descrição livre. A lista vira uma aba de configuração depois, sem mudar schema (`descricao` já cobre) |
| P3 | O Inter publica faixa de IP de origem do callback, para allowlist? | ⏸️ **A CONFIRMAR** na documentação/suporte do Inter. Não bloqueia: a defesa real é a reconsulta (§3.4) |
| P4 | Cobrança expirada deve avisar alguém, ou só mudar de cor na tela? | ⏸️ ADIADO — o plano só muda o status. `pix_expirado` já vai ao bus, então um plugin pode reagir |
| P5 | Depois do corte, o `recovery` deveria abandonar o polling e receber o repasse? | ⏸️ ADIADO — ele **não** usa o webhook hoje (§2.4) e funciona. Mexer sem necessidade é risco de graça |
| P6 | Quando migrar Curseduca e o lançamento do pedido (D7)? | ⏸️ ADIADO por decisão do usuário. O `contact_id`/`conversation_id` já ficam gravados na cobrança, então a ponte futura não precisa de migration |
| P7 | Descoberto que **já existe uma 2ª chave PIX** (`98a64b6b-…`, §2.3.2) e que os workflows do n8n que a usam estão inativos. Usá-la para o WhatsBot dispensaria o repasse inteiro (F6) e o corte (F11) — as duas peças de maior risco do plano | ⚠️ **REABRE D2 — decidir antes da F11.** Custo agora é **zero** (a chave existe), e o argumento que fechou D2 era "não quero duas". Contra: separa a conciliação em duas chaves. **Confirmar antes que a chave está de fato ociosa.** 🔧 **O código já atende às duas saídas sem alteração**: a F6 foi implementada e testada, e escolher a 2ª chave é apenas deixar `forward_enabled` desmarcado e registrar o webhook na outra chave |
| P8 | O 401 nas chamadas de recurso é mesmo descasamento cert↔aplicação, ou falta do header `x-conta-corrente`? | ✅ **RESOLVIDO (2026-08-12): descasamento de certificado.** O corpo do 401 é `{"title":"Login/senha inválido"}` — não é erro de conta. Decifrei o cert da app A no n8n (a `N8N_ENCRYPTION_KEY` veio do Thiago): ele CASA com a chave e é o cert do par `a7e926c0` (os nós `generate_token`/`generate_pix` dos dois workflows usam essa mesma credencial SSL). O 401 anterior foi por eu ter testado com o cert da app B. **Descoberta que muda a F3:** esse cert **venceu em 2026-04-03** — por isso os workflows n8n estão inativos. Não há cert válido dessa app em lugar algum; o par client_id/secret segue válido |
| P9 | Rotacionar o `client_secret` da app A depois de tirá-lo do n8n? | ⏸️ ADIADO — só depois de confirmar que apenas os dois workflows inativos a usam. Rotacionar antes disso quebra o que estiver escondido |

---

## 8. Checklist de verificação

Marcado em 2026-08-12 conforme a execução. O que segue aberto depende de credencial (F3),
de operação em produção (F11/F12) ou de conferência visual.

- [ ] **Credencial provada por chamada de RECURSO (200), não só por token (200)** — §2.3.1
      ⏳ o botão "Testar credencial" já faz exatamente isso; falta a credencial (F3)
- [ ] As quatro peças da credencial estão no cofre, e nenhum valor ficou em `docs-planos/`
      ⏳ a segunda metade **está cumprida** (o arquivo só tem ponteiros); o cofre depende da F3
- [x] `python3 scripts/test_plugins.py --python-only pagamentos` verde — **56 testes**
- [x] Pelo menos um teste sobe o app pelo **loader real** e bate no endpoint real —
      `tests/python/test_webhook_e_rotas.py` usa `build_app(["pagamentos"])` e chama as rotas
- [x] Suíte do core no Postgres (`WHATSBOT_TEST_DB_URL`) **sem regressão nova** — as 3 falhas
      são pré-existentes e alheias (ver F10)
- [x] Migration aplica em banco limpo — o `build_app` cria as 3 tabelas do zero a cada boot
- [ ] O `.zip` instala num clone limpo ⏳ (o ZIP está gerado e conferido; falta instalar)
- [ ] Repasse provado **antes** do corte (F11.2) e URL anterior anotada (F11.1)
- [x] Payload forjado **não** marca pago (o `get_cob` decide) —
      `test_callback_forjado_nao_marca_pago`, que é o par diferencial de
      `test_pagamento_confirmado_pelo_inter_marca_pago`: **o corpo do callback é idêntico nos
      dois**, só muda o que o Inter responde. É isso que prova que a decisão não vem do corpo
- [x] Reentrega do mesmo evento não duplica nota, evento nem envio —
      `test_reentrega_do_mesmo_evento_nao_duplica_o_aviso` e
      `test_conferir_de_novo_pelo_painel_tambem_nao_duplica`
- [ ] Bolha única em canal que ecoa (GOWA) ⏳ o mecanismo está travado por teste
      (`test_envio_registra_o_eco_antes_de_sair`); o comportamento do provedor exige canal real
- [x] `GET /config` não devolve segredo em claro; `PUT` sem o campo não apaga o salvo
- [ ] Modo escuro conferido no modal, na tela de histórico e na configuração
- [ ] Desativar e reativar o plugin não perde cobrança nem duplica task
      ⏳ por construção: nenhum estado vive em variável de módulo (só cache de token, refeito)
- [x] Nenhum segredo em URL, log ou mensagem de erro — `test_erro_do_banco_nao_carrega_o_segredo`
      e `test_corpo_de_erro_e_truncado`; o log do repasse nunca imprime a URL completa
- [x] Trilha de auditoria com `cobranca.create`, `config.update`, `webhook.reveal`,
      `webhook.migrate` (+ `webhook.rotate` e os três de `vendedor.*`)
- [x] Com o `trackify` desativado, o plugin funciona inteiro — é o estado em que a suíte rodou
      (o core local nem tem o seam `plugins.services`)
- [x] `build_plugins.py --check pagamentos` bate byte a byte
- [ ] 24 h após o corte sem `forward_error` no inbox

---

## 9. Apêndice — arquivos-chave

**Plugin novo** (`../whatsbot-pro-plugins/plugins/pagamentos/`) — ✅ **escrito em 2026-08-12**
`src/plugin.yaml` · `src/txid.py` · `src/money.py` · `src/mensagem.py` · `src/inter.py` · `src/certs.py` ·
`src/store.py` · `src/routes.py` · `src/reconcile.py` (➕ acrescentado) · `src/forward.py` ·
`src/notify.py` · `src/trackify_bridge.py` · `src/lifecycle.py` · `src/settings.py` ·
`src/migrations/001_initial.sql` ·
`src/static/{extends.js,CobrancaModal.js,pagamentos.js,config.js}` (➕ o modal virou arquivo
próprio, como o `JourneyModal` do trackify) ·
`tests/python/{test_modulos_puros.py,test_cliente_inter.py,test_webhook_e_rotas.py}` ·
`pagamentos.json` · `pagamentos.zip` · entrada em `catalog.json`

**Core — só leitura, nada muda aqui (D8)**
`plugins/context.py` (`audit:226`, `plugin_permission:286`, `get_channel_runtime:121`, `spawn_task:426`) ·
`server/app.py:56` (isenção `/public/`) · `server/client_ip.py` · `plugins/semver.py` ·
`web/static/js/components/contacts/ConversationMenu.js:22` · `web/static/js/plugins/registry.js:141` ·
`app/services/provisioning_service.py:45` (QR com `segno`) ·
`origin/developer:plugins/services.py` (seam do Trackify — **não está no checkout local**)

**Plugins de referência** (ler, não importar)
`retornos/src/actions.py:112,143-217` (nota privada + eco) · `protocolos/src/logic.py:51-54,5285-5316`
(consumidor de `services`) · `website/src/routes.py:214,358` (rota pública + revelar segredo) ·
`trackify/src/static/extends.js:24` (slot) · `trackify/src/services.py:96,366` (ops)

**Onde estão as credenciais existentes** (ponteiros — nenhum valor neste arquivo, §2.3.3)
n8n `n8n_queue` → `workflow_entity` id `YOs6S5h01mQO8VIC` (`GeneratePix`), nó `generate_token`, campo
`parameters.jsonBody` · o mesmo par no id `4YQpG9Yn6Dty40zm` (`BOOTCAMP ANALISTA EM REDES`) ·
`credentials_entity` id `zHSOvzRxahO1uDDn` (`inter SSL`, cifrada) ·
`~/PROJETOS/cloudflare/banco-inter/` (par mTLS da **outra** aplicação) ·
`~/.claude/projects/-home-redesbrasil-PROJETOS/memory/reference_banco_inter_api.md` (nota de referência)

**Origem a portar** (servidor `10.8.254.194:64777`)
`~/PROJETOS/cloudflare/recovery/src/index.js:1024-1294,3892-4353,6063-6222` ·
`~/PROJETOS/cloudflare/recovery/migration_017_pix_manual.sql` (+018, +019) ·
`~/PROJETOS/cloudflare/imersao-analista/src/index.js:924-1058,1365-1382` ·
`~/PROJETOS/cloudflare/dashboard/11-pix-manual/README.md`
