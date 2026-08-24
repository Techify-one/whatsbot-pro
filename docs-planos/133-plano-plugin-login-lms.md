# Plano 133 — Login da Escola (LMS) vira plugin: o link de acesso sai pelo canal do WhatsBot, não pela Evolution

> **Status:** PLANEJADO — nada implementado · **Data:** 2026-08-20 · **Escopo:** médio (plugin novo; **zero** mudança no core; o LMS só é tocado na fase de limpeza)
> **Origem:** pedido do usuário — transformar a automação de login por WhatsApp do LMS (`/opt/lms`, branch `main`, `8db74bb`) num plugin do WhatsBot Pro, com a **entrega da mensagem pelos canais do WhatsBot** em vez da Evolution API.
> **Método:** leitura do código real dos dois lados; todo `arquivo:linha` abaixo foi verificado em 2026-08-20. LMS em `/opt/lms`; core em `~/opt/whatsbot-pro`; plugins em `~/opt/whatsbot-pro-plugins`. Plugin de referência: `criar_conta` 2.1.0 (frase-gatilho → chamada externa → resposta pelo canal da própria conversa).
> **O quê/porquê:** hoje o LMS é, ele próprio, um mini-bot de WhatsApp: recebe webhook da Evolution numa rota pública, resolve o aluno, aprova o código de login e manda o link chamando a API da Evolution direto (`/opt/lms/backend/src/services/evolutionService.ts:27`). Isso deixa o LMS com uma responsabilidade que é do WhatsBot (falar com o cliente), duplica a camada de entrega e amarra o fluxo a um provedor só. Depois deste plano, o LMS volta a ser dono apenas de **aluno, token e código**; quem conversa é o canal do WhatsBot.
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| D1 ✅ (2026-08-20) | O plugin lê e escreve **direto no banco do LMS**. A credencial fica nas settings do plugin e **nasce vazia** | Sem endpoint novo no LMS: a fase 1 não exige deploy do LMS. Sem credencial, o plugin fica **inerte** (a mensagem segue o fluxo normal para a IA), igual ao token vazio do `criar_conta` (`plugins/criar_conta/src/settings.py`). Mitigação obrigatória: role dedicado no Postgres do LMS — §3.4 |
| D2 ✅ (2026-08-20) | O **número** do WhatsApp que atende o login é configurado no plugin; **só o canal que tiver aquele número responde** | Resolução por `channels.own_phone` / `channels.account_identity` (`db/tables.py:281` e `:287`). Nenhum casamento ⇒ **fail-closed**: plugin inerte, nunca "chuta" o canal único |
| D3 ✅ (2026-08-20) | O gatilho é **o código de login + uma lista de frases**, nunca catch-all | O `loginHandler` do LMS é catch-all (`matches: () => true`, `/opt/lms/backend/src/services/messageHandlers/loginHandler.ts:283`). Portar isso sequestraria TODA mensagem de qualquer contato que exista como aluno, e a IA/atendente nunca mais responderiam esses contatos |
| D4 ✅ (2026-08-20) | A automação da Evolution **fica no ar em paralelo** e só é removida depois do cutover validado | F9 é uma fase separada, com PR próprio no LMS |
| D5 (proposta, confirmar) | A resposta é enviada **sempre**, sem exigir o gate de IA da conversa | O aluno está parado na tela de login esperando o link; calar por causa de um atendente atribuído quebraria o fluxo. Fica um toggle `respeitar_gate_ia`, default `false` |
| D6 (proposta, confirmar) | O plugin **assume o turno**: grava a mensagem do aluno e a resposta, e aborta o pipeline | Mesmo desenho do `criar_conta` (`plugins/criar_conta/src/filters.py:112`): devolver `None` em `filter.message.before_save` impede save do lote **e** chamada ao LLM |
| D7 ✅ | **Zero mudança no core do WhatsBot** | Tudo no plugin, imports defensivos, tabela `plugin_lms_login_*` — regra do `CLAUDE.md` §"O que fica no core e o que vai pro plugin" |
| D8 ✅ | O **front do LMS não muda** | `LoginPage.tsx`, o polling de status e a tabela `login_codes` continuam iguais. Em produção muda **um setting**: `whatsapp_login_link` passa a apontar para o número do WhatsBot |

---

## 1. Resumo executivo

O fluxo de login da Escola é: a tela gera um código de 6 dígitos, monta um link `wa.me` com o texto `Codigo de login: NNNNNN`, o aluno manda essa mensagem no WhatsApp, alguém do outro lado casa o telefone com um aluno, aprova o código e devolve `{site_url}#token={access_token}`; a tela, que está em polling, detecta a aprovação e entra.

Hoje o "alguém do outro lado" é o próprio LMS falando com a Evolution. Depois deste plano é o plugin `lms_login` dentro do WhatsBot: ele intercepta a mensagem em `filter.message.before_save`, confirma que o canal é o número configurado, resolve o aluno e o código **no banco do LMS**, e responde pelo `OutboundRouter` — o mesmo caminho que qualquer resposta do painel usa, portanto funciona em GOWA ou WhatsApp Cloud sem `if provider ==`.

O ganho não é só trocar de provedor: a bolha da conversa passa a existir no atendimento (hoje o diálogo de login acontece fora do WhatsBot, invisível para o operador), a mensagem fica auditável, e some a rota pública sem autenticação do LMS (§2.4).

---

## 2. Como funciona hoje (mapa verificado)

### 2.1 O ciclo completo

| # | Onde | O que acontece |
|---|---|---|
| 1 | `/opt/lms/frontend/src/pages/LoginPage.tsx:17` | A tela pede `POST /api/auth/login-code`, recebe um código de 6 dígitos (validade 5 min) e o **acrescenta ao texto** do link `wa.me`: `"\n\nCodigo de login: " + code` |
| 2 | `/opt/lms/backend/src/controllers/loginCodeController.ts:13` | `generateLoginCode` limpa códigos com mais de 30 min, sorteia um código único entre os `pending` e grava em `login_codes` |
| 3 | Evolution | O aluno envia a mensagem; a Evolution chama `POST /api/evolution/webhook` |
| 4 | `/opt/lms/backend/src/controllers/evolutionController.ts:25` | Valida o payload com Zod, ignora `fromMe`, responde 200 na hora e processa em background por um registry de handlers |
| 5 | `/opt/lms/backend/src/services/messageHandlers/loginHandler.ts:166` | Extrai o código por regex configurável, acha o aluno pelo telefone, monta o link, aprova o código e responde |
| 6 | `/opt/lms/backend/src/services/evolutionService.ts:27` e `:68` | `sendText` (`POST /message/sendText/{instance}`) e `closeSession` (`POST /n8n/changeStatus/{instance}`) |
| 7 | `/opt/lms/backend/src/controllers/loginCodeController.ts:54` | A tela, em polling de 1 s, vê `approved`, **consome** o código atomicamente e recebe o `access_token` |

### 2.2 Regras de negócio que precisam ser preservadas

Todas vivem em `loginHandler.ts`:

- **Busca do aluno pelo telefone com fallback do 9º dígito** (`:134`): `phone ILIKE %numero%`, depois com o 9 inserido (12 dígitos começando em 55) e depois com o 9 removido (13 dígitos).
- **Link** = `{site_url}#token={access_token}` (`:218`), com `site_url` lido da tabela `settings` (`:116`); ausente ⇒ mensagem de erro interno.
- **Aprovação do código** (`:250`): `UPDATE login_codes SET status='approved', access_token=..., approved_at=NOW() WHERE id=... AND status='pending'`, depois de checar `expires_at` em JavaScript.
- **Código vencido** vira `status='expired'` e o link é enviado assim mesmo.
- **Código inexistente/já consumido**: envia o link assim mesmo, com outra mensagem.
- **Sem código na mensagem**: envia o link (login direto).
- **`access_token` NULL** = acesso revogado ⇒ recusa.
- **Mensagens e a regex são configuráveis** pelo setting `evolution_login_config` (`/opt/lms/backend/migrations/047_add_evolution_login_config.sql`), com placeholders `{{name}}` e `{{link}}`; quando o template não contém `{{link}}`, o link vai numa **segunda mensagem** (`loginHandler.ts:74`).

### 2.3 O que o WhatsBot já oferece (ganchos verificados)

- **`filter.message.before_save`** — `app/services/message_ingest_service.py:482`, com o dict montado em `:464`: traz `phone`, `channel_id`, `text`, `msg_id`, `is_group`, `media_type`, `raw`. Devolver `None` aborta save **e** LLM.
- **`OutboundRouter.send_text(channel_id, chat_id, text)`** — `channels/outbound.py:114`: entrega pelo canal da própria conversa, com decisões por **capability** e nunca por nome de provider.
- **Identidade do canal** — `channels.own_phone` (`db/tables.py:281`) e `channels.account_identity` + `account_identity_kind` (`:287`), preenchidos pelo sweep pós-conexão; GOWA publica `AccountIdentity("phone", ...)` em `channels/providers/gowa_channel.py:216` e o WhatsApp Cloud grava `own_phone` a partir do display number.
- **`plugins.context`** — `broadcast` (`:155`), `make_plugin_db` (`:183`), `get_channel_runtime` (`:130`), `audit` (`:235`).
- **Settings declarativas** — Pydantic em `settings.py`, persistidas como `plugin.<id>.<campo>`; o form é gerado pelo core.
- **Versão da API** — `plugins/semver.py:90` está em `1.8.0`; o manifesto deste plugin declara `">=1.0,<2.0"` porque nada aqui exige gancho novo.

### 2.4 Dois achados do lado do LMS

1. **Rota pública sem autenticação com credenciais no corpo.** `POST /api/evolution/webhook` não tem middleware de auth e recebe `serverUrl` + `apiKey` + `instanceName` **no próprio payload** (`evolutionController.ts:13-24`). Quem souber a URL pode postar um `remoteJid` de um telefone que exista na base e um `serverUrl` apontando para um servidor próprio: o `sendText` com o `access_token` do aluno vai para esse servidor. **A F9 fecha isso ao remover a rota** — é motivo suficiente para não deixar a limpeza indefinidamente para depois.
2. **O evento `whatsapp_student_not_found`** (`loginHandler.ts:188`) é o único sinal que o fluxo manda para fora quando o telefone não é aluno. Ele passa por `sendWebhook` (`/opt/lms/backend/src/services/webhookService.ts:175`): se o setting `webhook_url` estiver vazio, **nada acontece**; se estiver preenchido, faz POST com até 3 tentativas (backoff 1s/2s/4s) e grava cada tentativa em `webhook_logs`. É o único evento do LMS sem o objeto `triggered_by` e não está documentado na tabela de eventos do `CLAUDE.md` do LMS. **Nenhum consumidor local** (varredura em `whatsbot-pro`, `whatsbot-pro-plugins`, `nexus-*`, `windmill-*`, `landing-page`) trata esse nome. Ver P4 em §6.

---

## 3. Desenho do plugin

### 3.1 Manifesto e arquivos

```
~/opt/whatsbot-pro-plugins/plugins/lms_login/
├── src/
│   ├── plugin.yaml
│   ├── __init__.py
│   ├── filters.py             # gatilho + orquestração (molde: criar_conta/src/filters.py)
│   ├── lms.py                 # engine e queries do banco do LMS — módulo FOLHA, puro de core
│   ├── conf.py                # settings do plugin, cache 30 s, variantes BR do telefone
│   ├── delivery.py            # canal, dedup de eco, envio, save + broadcast
│   ├── settings.py            # Pydantic (botão "Configurar" do card)
│   └── migrations/001_initial.sql
└── tests/python/test_lms_login.py
```

```yaml
id: lms_login
name: Login da Escola (LMS)
version: 1.0.0
whatsbot_api_version: ">=1.0,<2.0"
entry:
  filters: filters
  settings: settings
filters:
  - filter.message.before_save
migrations: migrations
permissions: []
dependencies: []
```

### 3.2 Fluxo do filtro (cortes baratos primeiro — roda em toda mensagem recebida)

1. **Corte puro, sem banco:** texto não vazio, `is_group == False`, e casa a regex do código **ou** uma das frases-gatilho. Não casou ⇒ devolve `msg` intacto.
2. **Settings** (cache de 30 s sobre a tabela `config` do WhatsBot, padrão de `criar_conta/src/conf.py`): sem credencial de banco ou sem número configurado ⇒ devolve `msg` intacto, com um WARNING.
3. **Canal:** `msg["channel_id"]` tem de ser o canal do número configurado (§3.3). Não é ⇒ devolve `msg` intacto — naquele número a IA responde normalmente.
4. **Assume o turno:** grava a mensagem do aluno com `save_and_broadcast(contact, "user", ...)`; ao final devolve `None`.
5. **Anti-abuso:** consulta `plugin_lms_login_requests`; estourou cooldown/teto ⇒ registra e não responde.
6. **Banco do LMS:** resolve aluno → resolve `site_url` → aprova/expira o código (§3.4).
7. **Responde** pelo `OutboundRouter`, marcando os caches de eco do core (`state.recently_sent` e `state.processed_messages`) antes do envio — quem manda por fora do `messaging_service` precisa disso, senão a própria mensagem volta como inbound e vira uma segunda bolha.
8. **Persiste e audita:** grava a bolha com `sent_by_name = "Login da Escola"`, registra a linha na tabela do plugin e chama `audit("lms_login", "acesso.enviar", ...)`.

### 3.3 Resolução do canal pelo número (D2)

Com cache de 30 s, sobre o banco do **WhatsBot**:

```sql
SELECT id, provider, own_phone, account_identity, account_identity_kind
  FROM channels
 WHERE enabled = 1 AND archived = 0
```

Compara os dígitos de `own_phone` e o `account_identity` de `kind='phone'` com as **variantes BR** (12↔13 dígitos) do número configurado — a mesma regra do 9º dígito do `loginHandler`. A função pura tem ~10 linhas e é **copiada** para dentro do plugin: `channels/br_phone.py:12` existe no core mas não é API declarada, e copiar mantém o ZIP autossuficiente (precedente: os canais Meta carregam cópia própria da base Graph).

Nenhum canal casou ⇒ inerte, com WARNING. Mais de um casou ⇒ inerte também: é sinal de configuração errada, e responder pelo número errado não tem desfazer.

### 3.4 Acesso ao banco do LMS (`lms.py`)

Engine SQLAlchemy própria (`postgresql+psycopg`), pool pequeno, `connect_timeout` curto, criada preguiçosamente e recriada quando as settings mudam. Módulo **folha**: não importa nada do core, o que o torna testável sem subir o app.

```sql
-- 1) aluno pelo telefone (variantes BR montadas em Python)
SELECT id, name, phone, access_token FROM students
 WHERE phone ILIKE ANY(:patterns) LIMIT 1;

-- 2) URL base do site
SELECT value FROM settings WHERE key = 'site_url' LIMIT 1;

-- 3) aprovar o código — atômico, só se pendente E não vencido
UPDATE login_codes SET status='approved', access_token=:token, approved_at=NOW()
 WHERE id = (SELECT id FROM login_codes
              WHERE code=:code AND status='pending'
              ORDER BY created_at DESC LIMIT 1)
   AND expires_at > NOW()
RETURNING id;

-- 4) só se (3) não devolveu nada: existe pendente vencido?
UPDATE login_codes SET status='expired'
 WHERE code=:code AND status='pending' AND expires_at <= NOW()
RETURNING id;
```

Duas melhorias sobre o que existe hoje, de graça: a aprovação vira **uma** instrução (o LMS faz `SELECT` e depois `UPDATE`, em duas viagens) e a comparação de expiração roda **no banco**, com `NOW()`, eliminando divergência de relógio entre os dois servidores.

**Role dedicado (obrigatório — mitigação da D1).** O plugin nunca usa o usuário dono do banco:

```sql
CREATE ROLE lms_whatsbot LOGIN PASSWORD '...';
GRANT CONNECT ON DATABASE <db> TO lms_whatsbot;
GRANT USAGE  ON SCHEMA public TO lms_whatsbot;
GRANT SELECT  ON students, settings TO lms_whatsbot;
GRANT SELECT, UPDATE ON login_codes TO lms_whatsbot;
```

O `access_token` **nunca** entra em log, broadcast, auditoria ou tabela do plugin — ele só existe no corpo da mensagem enviada ao próprio aluno.

### 3.5 Settings (`plugin.lms_login.*`)

| Campo | Default | Observação |
|---|---|---|
| `numero_do_canal` | `""` | DDI+DDD+número que atende o login. Vazio ⇒ inerte |
| `lms_host`, `lms_port`, `lms_database`, `lms_user` | `""` / `5432` | Conexão com o Postgres do LMS |
| `password` | `""` | **Nome exato de propósito.** O sanitizador da auditoria compara o nome da chave por igualdade (`db/repositories/audit_repo.py:21`), e o diff de `plugin.settings.changed` carrega os valores (`server/routes/plugins.py:328`). `lms_password` apareceria em claro na trilha; `password` sai como `***` |
| `codigo_regex` | `Codigo de login:\s*(\d{6})` | Igual ao `evolution_login_config` de hoje |
| `frases_gatilho` | `Me envie o Link da Escola da Automação` | Uma por linha, casamento aparado e case-insensitive |
| `msg_login_aprovado`, `msg_login_direto`, `msg_codigo_expirado`, `msg_codigo_invalido`, `msg_aluno_nao_encontrado`, `msg_acesso_revogado`, `msg_erro_interno` | textos da migration 047 do LMS | Placeholders `{{name}}` e `{{link}}` |
| `enviar_link_separado` | `true` | Quando o template não tem `{{link}}`, manda o link numa segunda mensagem (paridade) |
| `respeitar_gate_ia` | `false` | Ligado, só responde se a IA puder falar (global + canal + conversa + sem atendente) |
| `encerrar_conversa` | `false` | Único equivalente possível ao `closeSession` da Evolution |
| `cooldown_segundos` / `max_por_hora` | `60` / `6` | Anti-abuso por telefone |
| `site_url_override` | `""` | Vazio ⇒ lê `settings.site_url` do LMS |

### 3.6 Paridade de casos

| Situação | Hoje (Evolution) | No plugin |
|---|---|---|
| Código válido | aprova + `login_approved` + link | idêntico |
| Código vencido | marca `expired` + `code_expired` + link | idêntico |
| Código inexistente/já usado | `code_invalid` + link | idêntico |
| Sem código, frase-gatilho | `direct_login` + link | idêntico |
| Aluno não encontrado | responde + webhook `whatsapp_student_not_found` | responde; webhook depende de P4 |
| `access_token` NULL | `access_revoked` | idêntico |
| `site_url` ausente | `internal_error` | idêntico |
| Qualquer outra mensagem de um aluno | catch-all: mandava o link | **segue o fluxo normal** (D3) |

### 3.7 Tabela do plugin, auditoria e regras duras

```sql
CREATE TABLE IF NOT EXISTS plugin_lms_login_requests (
  id SERIAL PRIMARY KEY,
  ts DOUBLE PRECISION NOT NULL,
  channel_id TEXT NOT NULL,
  phone TEXT NOT NULL,
  code TEXT,
  status TEXT NOT NULL,   -- approved | code_expired | code_invalid | student_not_found
                          -- | access_revoked | cooldown | error
  student_id TEXT,
  delivered BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_plugin_lms_login_requests_phone_ts
  ON plugin_lms_login_requests (phone, ts);
```

Auditar `lms_login.acesso.enviar` **não** viola a regra dura "conversa nunca entra na trilha": o que se registra é a entrega de uma credencial de acesso (ação com efeito externo), não o tráfego de mensagens — a bolha em si não gera linha. Mesmo precedente do `conta.criar` no `criar_conta`.

Regras duras do plugin: nunca responde em grupo; nunca responde em canal diferente do configurado; nunca age sem credencial; nunca loga o token.

### 3.8 O que **não** é portado

- **`closeSession`** (`POST /n8n/changeStatus`): não tem equivalente e não precisa ter — abortar o pipeline já cala a IA para aquela mensagem. Quem quiser o efeito "encerra o atendimento" liga `encerrar_conversa`.
- **O catch-all** do `matches: () => true` — substituído pelo gatilho explícito (D3).
- **A rota pública** e o registry de handlers do LMS: morrem na F9.

---

## 4. Fases

### F0 — Preparação (fora do código)
1. Criar o role `lms_whatsbot` no Postgres do LMS com os grants de §3.4.
2. Liberar a rota de rede do container do WhatsBot até o Postgres do LMS (Coolify) e confirmar com um `SELECT 1`.
3. Definir qual canal/número vai atender o login.

**Status de execução:** NÃO INICIADA · **Bloqueia:** F2 em diante · **Dono:** usuário

### F1 — Esqueleto do plugin
`/new-plugin` no `whatsbot-pro` gerando `plugins/lms_login/` com manifesto, `settings.py`, `conf.py` (cache 30 s + variantes BR) e `migrations/001_initial.sql`. Sem lógica de negócio ainda; o plugin carrega, aparece no card e é inerte.

**Status de execução:** NÃO INICIADA

### F2 — `lms.py`: leitura e escrita no LMS
Engine, as quatro queries de §3.4, decisão pura `(aluno, código, site_url) → status + variáveis da mensagem`. Nenhum import do core.

**Status de execução:** NÃO INICIADA

### F3 — `delivery.py` + `filters.py`: entrega no canal
Resolução do canal pelo número (§3.3), envio pelo `OutboundRouter`, marcação dos caches de eco, `save_and_broadcast`, e o `filter.message.before_save` com os cortes baratos na ordem de §3.2.

**Status de execução:** NÃO INICIADA

### F4 — Anti-abuso, auditoria e trilha
Cooldown por telefone, tabela `plugin_lms_login_requests`, `audit("lms_login", "acesso.enviar", ...)`.

**Status de execução:** NÃO INICIADA

### F5 — Testes
`python3 scripts/test_plugins.py lms_login`. Puros: casamento de gatilho (código, frase, ruído), variantes BR, render de template, decisão a partir de linhas simuladas, resolução de canal por número. **Mais um teste que sobe o app pelo loader real e injeta um inbound** — teste que só importa o módulo por caminho continua verde com a costura arrancada.

**Status de execução:** NÃO INICIADA

### F6 — Empacotar e publicar
`python3 scripts/build_plugins.py lms_login`, `--check`, entrada em `catalog.json`, `lms_login.json` e no README do repo de plugins. Fonte, testes, metadados e ZIP no mesmo commit.

**Status de execução:** NÃO INICIADA

### F7 — Validação em staging
Importar o `.zip`, configurar, parear o número de teste e percorrer os 7 casos de §3.6 com a LoginPage real (código gerado pela tela, redirect do navegador ao final).

**Status de execução:** NÃO INICIADA

### F8 — Cutover em produção
Trocar o setting `whatsapp_login_link` do LMS para o número do WhatsBot (`/opt/lms/backend/migrations/017_add_whatsapp_login_link.sql` é só o seed; o valor vivo está na tabela `settings`). Monitorar `plugin_lms_login_requests` e `webhook_logs`.

**Status de execução:** NÃO INICIADA

### F9 — Limpeza no LMS (PR próprio)
Remover `routes/evolution.ts`, `controllers/evolutionController.ts`, `services/evolutionService.ts`, `services/messageHandlers/` e o setting `evolution_login_config`; atualizar o `CLAUDE.md` do LMS (regra obrigatória do repo). **Fecha o furo da rota pública** descrito em §2.4.1. Sugestão de commit: `refactor(auth): remove evolution whatsapp login handler`.

**Status de execução:** NÃO INICIADA

### F10 — Opcionais (só se pedidos)
Tela de histórico dos envios para o suporte ("o aluno diz que não recebeu"); caminho do operador por nota privada com "IA lê" (padrão `criar_conta/src/filters.py:on_llm_messages`); outros canais — Telegram/site exigem outra chave de identidade, porque lá o `phone` é o chat_id.

**Status de execução:** NÃO INICIADA

---

## 5. Riscos

| # | Risco | Mitigação |
|---|---|---|
| R1 | **Schema do LMS é contrato implícito.** Renomear coluna em `students`/`login_codes` quebra o plugin em silêncio | Teste de fumaça ao salvar as settings (valida as 4 queries) + nota na seção de banco do `CLAUDE.md` do LMS |
| R2 | **Duas implementações da mesma regra** enquanto Evolution e plugin coexistem (F7–F8) | Janela curta por desenho; F9 elimina uma |
| R3 | **Rede/credencial indisponível** entre WhatsBot e Postgres do LMS | É o único pré-requisito bloqueante — F0 antes de qualquer código |
| R4 | **O link é credencial permanente** entregue a quem escrever daquele número | Cooldown, recusa em grupo, canal único, trilha de auditoria. Risco herdado do desenho atual, não introduzido aqui |
| R5 | Plugin desligado ⇒ ninguém atende o login | O toggle derruba o processo e o novo boot já sobe sem o filtro; monitorar `plugin_lms_login_requests` vazio após o cutover |

---

## 6. Perguntas em aberto

- **P1 — Rede e credencial (bloqueante):** o Postgres do LMS aceita conexão do container do WhatsBot? O role `lms_whatsbot` será criado como em §3.4?
- **P2 — Frases-gatilho:** além de "Me envie o Link da Escola da Automação" (texto atual do `wa.me`), quais entram no default?
- **P3 — Anti-abuso:** 6 envios/hora por telefone com 60 s entre eles serve? Ao estourar: silêncio ou mensagem "aguarde um instante"?
- **P4 — `whatsapp_student_not_found`:** manter o disparo (o plugin faria POST no `webhook_url` do LMS) ou basta a linha em `plugin_lms_login_requests`? Decidir com:
  ```sql
  SELECT value FROM settings WHERE key = 'webhook_url';
  SELECT created_at, http_status, succeeded, error_message
    FROM webhook_logs WHERE event_type = 'whatsapp_student_not_found'
    ORDER BY created_at DESC LIMIT 20;
  ```
  Sem linhas ou tudo com `succeeded = false` ⇒ evento morto, não reproduzir.
- **P5 — D5/D6:** confirmar "responde sempre" (gate de IA desligado por padrão) e "o plugin assume o turno".

---

## 7. Referências

**LMS** (`/opt/lms`): `backend/src/services/messageHandlers/loginHandler.ts` · `backend/src/controllers/evolutionController.ts` · `backend/src/services/evolutionService.ts` · `backend/src/routes/evolution.ts` · `backend/src/controllers/loginCodeController.ts` · `backend/src/services/webhookService.ts` · `frontend/src/pages/LoginPage.tsx` · migrations `017`, `040`, `041`, `046`, `047`.

**Core:** [app/services/message_ingest_service.py](../app/services/message_ingest_service.py) · [channels/outbound.py](../channels/outbound.py) · [channels/base.py](../channels/base.py) · [channels/br_phone.py](../channels/br_phone.py) · [channels/providers/gowa_channel.py](../channels/providers/gowa_channel.py) · [db/tables.py](../db/tables.py) · [plugins/context.py](../plugins/context.py) · [plugins/semver.py](../plugins/semver.py) · [db/repositories/audit_repo.py](../db/repositories/audit_repo.py) · [server/routes/plugins.py](../server/routes/plugins.py) · [docs/PLUGINS_AUDITAVEIS.md](../docs/PLUGINS_AUDITAVEIS.md).

**Plugins:** `~/opt/whatsbot-pro-plugins/plugins/criar_conta/src/{filters,delivery,conf,settings,account}.py` (molde completo) · `scripts/build_plugins.py` · `scripts/test_plugins.py`.
