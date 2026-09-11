# API REST e eventos WebSocket do WhatsBot

> Guia da superfície HTTP/WS do backend. O [`CLAUDE.md`](../CLAUDE.md) carrega a **regra curta** e os avisos ⚠️;
> aqui está o **porquê**, o histórico e o detalhe. Texto migrado do `CLAUDE.md` no plano 139
> — nada foi reescrito na migração, só realocado.

As rotas vivem em `server/routes/`; esta tabela é o índice navegável. Formato de resposta
REST: `{"ok": bool, "data": ..., "error": ...}`.

---

## API REST do WhatsBot (backend FastAPI)

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| GET | `/` | Serve o frontend (web/index.html) |
| GET | `/wizard` | Serve o frontend forçando o wizard de 1ª execução (onboarding) |
| GET | `/api/config` | Retorna config (API key mascarada) + `account_url` + `public_base_url`. **Efeito colateral**: captura e persiste `public_base_url` (config key global — a URL que o operador usa pra acessar o painel, honrando headers de proxy reverso) no 1º acesso, com self-heal se o salvo era localhost. Variável reutilizável por qualquer feature (ex.: o alerta de desconexão do plugin gowa monta o link de reconexão a partir dela) |
| PUT | `/api/config` | Salva config + atualiza AgentHandler |
| POST | `/api/config/test-key` | Testa API key no proxy Techify (compatível OpenRouter); auto-salva se válida |
| POST | `/api/config/request-apikey` | Provisiona uma chave via Techify (manda msg ao número de provisionamento; usado pelo wizard) |
| GET | `/api/models` | Lista de modelos do proxy (cache 10 min) |
| GET | `/api/balance` | Saldo de crédito atual + threshold + `account_url` (recarga). Updates live via WS `low_balance` |
| GET | `/api/status` | Status de conexão + contagem de msgs |
| GET | `/api/qr` | QR code como PNG (204 se indisponível) |
| POST | `/api/whatsapp/reconnect` | Reconectar GOWA |
| POST | `/api/whatsapp/logout` | Logout GOWA |
| POST | `/api/webhook` | Recebe mensagens do GOWA (webhook) |
| GET | `/api/contacts?archived=true` | Lista apenas contatos/grupos arquivados |
| GET | `/api/contacts/unread-count` | Total de mensagens não lidas (badge global) |
| POST | `/api/contacts/{phone}/pin` | Fixa/desafixa a conversa (`{pinned}`). Fixadas vão pro topo da lista. WS `contact_pinned` |
| POST | `/api/contacts/{phone}/unread` | Marca a conversa como não lida (manual) |
| POST | `/api/contacts/mark-all-read` | Zera não lidas de todas as conversas |
| POST | `/api/contacts/mark-all-unread` | Marca todas as conversas como não lidas |
| POST | `/api/contacts/{phone}/messages/react` | Reage a uma mensagem com emoji (string vazia remove). WS `message_reaction` |
| POST | `/api/contacts/{phone}/messages/delete` | Apaga mensagem (revoke pra todos). WS `message_revoked`/`message_deleted`. Só aparece na UI em canais com a capability `revoke` (GOWA + Telegram sim; WhatsApp Cloud não) |
| POST | `/api/contacts/{phone}/messages/edit` | Edita o texto de uma mensagem de SAÍDA (operador/IA) já enviada. Body `{msg_id, text, conversation_id?}`. Só texto, roteado pelo canal via `outbound.edit_text` (capability `edit_message`); grava `edited_ts` + WS `message_edited`. Suportado em GOWA (`/message/{id}/update`) + Telegram (`editMessageText`); **WhatsApp Cloud NÃO** edita nem apaga (ambas as capabilities off — único canal sem as duas opções no menu) |
| GET | `/api/contacts/{phone}/members` | Lista participantes do grupo com nomes resolvidos (autocomplete de @menção) |
| GET | `/api/atendimentos/{id}/messages` | Thread de UMA conversa. Paginação keyset `before_id` + **janela ancorada** `after_id`/`around_id`/`at_ts` (plano 99, mutuamente exclusivos). Resposta: `has_more`(=`has_more_older`), `has_more_newer`, `anchor_id`, `marked_read` |
| GET | `/api/atendimentos/{id}/messages/search?q=` | Busca de texto DENTRO da conversa → `{matches:[{id,ts,role,snippet}], total}`, mais recente primeiro. `q` < 3 chars ⇒ vazio com 200. Gate `conversation.read` |
| GET | `/api/webhook-payloads?limit=N` | Últimos N payloads raw do webhook (debug, max 50) |
| GET | `/api/gowa-logs?limit=N` | Tail do `logs/gowa.log` (stdout/stderr do subprocess GOWA, só populado com `WHATSBOT_GOWA_DEBUG=1`) |
| GET | `/api/tools` | Lista todas as tools registradas (core + plugin) com estado de override |
| PUT | `/api/tools/{name}` | Atualiza override `{enabled?, description?, display_label?}`; `description=null` reseta |
| GET | `/api/plugins` | Lista todos os plugins descobertos com status (ativo/inativo/erro) |
| GET | `/api/plugins/manifest` | Manifest público dos plugins ativos (pro frontend dinâmico) |
| POST | `/api/plugins/{id}/enable` | Ativa o plugin e dispara restart |
| POST | `/api/plugins/{id}/disable` | Desativa o plugin e dispara restart |
| GET/PUT | `/api/plugins/{id}/settings` | Schema Pydantic + values do plugin (settings declarativas) |
| GET | `/api/plugins/{id}/export` | Baixa o plugin como `.zip` |
| POST | `/api/plugins/import` | Importa um plugin via upload de `.zip` |
| DELETE | `/api/plugins/{id}` | Remove a pasta + tabelas `plugin_<id>_*` + settings namespaceadas |
| POST | `/api/plugins/restart` | Restart manual do servidor |
| `*` | `/api/plugins/{id}/*` | Endpoints REST mountados pelo plugin (router próprio) |
| GET | `/api/admin/database` | Info do backend atual (dialect, URL redacted, caminho do config) |
| POST | `/api/admin/repair-sequences` | Re-ancora as sequences do Postgres em MAX(pk) (recovery pós-import manual) |
| WS | `/ws` | WebSocket para eventos real-time |

Formato de resposta REST: `{"ok": bool, "data": ..., "error": ...}`

Eventos WebSocket (frontend): `{"event": "...", "data": {...}}` — inclui `status`, `qr_update`, `gowa_status`, `config_saved`, `new_message`, `message_reaction`, `message_revoked`, `message_deleted`, `message_edited` (`{phone, msg_id, db_id, content, edited_ts}` — mensagem editada, seja de SAÍDA pelo operador/IA OU INBOUND quando o cliente edita a própria mensagem no WhatsApp/Telegram; o painel troca o conteúdo in-place e mostra "editada". WhatsApp Cloud não emite edição inbound), `contact_pinned`, `group_participants_changed`, `avatar_updated` (`{phone, v}` — `v` = mtime do arquivo, usado pra cache-bust da foto), `low_balance` (saldo abaixo do threshold → abre o modal de recarga), `ai_typing` (`{phone, channel_id, active}` — a IA está processando uma resposta para a conversa; o painel mostra "IA respondendo…" no header para o operador não responder por cima), `operator_typing` (`{phone, channel_id, conversation_id, user_id, user_name, active}` — OUTRO atendente está digitando naquela conversa; a linha da sidebar mostra "Fulano está digitando…" para os demais operadores logados. Ver "Indicador de digitação entre atendentes").

---

## API para integrações externas — chave por usuário (`X-Api-Key`)

Até este plano o WhatsBot tinha UMA superfície de API (`/api/*`), feita para o painel Preact e autenticada por sessão opaca de navegador. Não havia como um sistema externo (CRM, automação) integrar de forma programática, identificável e auditável.

**O insight que orienta o desenho: a chave é apenas um CRACHÁ novo que resolve para o mesmo `request.state.user` que uma sessão resolve.** Feito isso no middleware ([server/app.py](../server/app.py), logo depois da tentativa de sessão), RBAC, auditoria (ator), escopo por inbox e o gating das rotas de plugin **funcionam sem alteração** — a chave "vira o usuário". [server/authz.py](../server/authz.py) **não foi tocado**.

- **Formato**: `wsk_live_<prefix>.<secret>` — cabeçalho `X-Api-Key`, separado do `Authorization` DE PROPÓSITO (o middleware nunca confunde crachá de sessão com crachá de chave). ⚠️ O separador é `.` e o prefixo é **hexadecimal**: `secrets.token_urlsafe` usa o alfabeto base64url, que inclui `_`, e enquanto `_` separava os campos ~1 em 3 chaves nascia "malformada" e era recusada aleatoriamente. `.` não pertence ao alfabeto, então o parsing é total ([server/api_keys.py](../server/api_keys.py), travado por `test_generate_key_survives_the_base64url_alphabet`).
- **O segredo NUNCA é persistido**: só o Argon2 `key_hash` em `api_keys`. Ele aparece **uma única vez**, na resposta de `POST /api/api-keys` — não há endpoint que o leia de volta. `prefix` é o pedaço público (indexado) por onde a linha é encontrada.
- **Verificação**: o Argon2 é caro de propósito (~50-100ms), e uma integração faz muitas chamadas com a MESMA chave. Cacheamos só o resultado do **compare** por 60s; a **autorização não é cacheada** — a linha é relida do banco a cada request, então revogar/expirar vale na hora (travado por `test_verify_cache_does_not_bypass_revocation`).
- **A chave vale em TODO `/api/*`** (D2), inclusive `/api/plugins/<id>/*`. Como `plugin_permission`/`core_permission` passam pelo mesmo `authz.acheck`, **toda rota de plugin já criada responde à chave sem uma linha de código no plugin**.
- **Sem escopo POR CHAVE** (D3): a chave herda TODAS as permissões do dono. O controle é criar um **usuário dedicado** por integração — `users.custom_permissions=1` (os grants explícitos substituem papéis e desligam o short-circuit de admin) + membresia só nas caixas que ela deve enxergar. **A membresia de inbox vira o escopo de DADOS da chave automaticamente** (`visible_inbox_ids` já escopa contatos e conversas), sem nada de novo. A coluna `api_keys.scopes` existe nullable e SEM USO — deixa a porta aberta sem custo; adicionar escopo depois vira código, não migração (o ponto de enxerto seria `_rbac_allows`).
- **Auditoria distingue a procedência** (D4): `actor_type="apikey"` + `audit_log.api_key_id`. O **ator continua sendo o usuário dono** (a ação é dele); a chave é por onde ela entrou. Propagado por `ActorCtx` → `audit_listener.record`/`audit_event_handler` → `audit_repo.add` (⚠️ quem escreve a linha é o REPO — mexer só no listener não grava nada). A tela `/audit` filtra por chave (`?api_key=<id>`) e resolve o rótulo mesmo depois de revogada (a revogação é SOFT).
- **A única permissão nova é `apikey.manage`** (D5), e ela governa *emitir/revogar* chave, **nunca** *usar* a API: quem pode fazer algo no painel pode fazer via chave. Ela NÃO entra em `ROLE_DEFAULTS` ([server/permissions.py](../server/permissions.py)); o admin recebe pelo short-circuit de role. **`apikey.manage` é permissão sobre SI MESMO**: emitir, listar e revogar valem só para as próprias chaves. Escolher OUTRO dono exige `users.manage` — ver o guardrail 0 abaixo.

### Guardrails de emissão (o que D2+D3 tornam obrigatório)

Como a chave vale em todo `/api/*` **e** herda tudo do dono, o controle inteiro se concentra no momento de emitir ([server/routes/api_keys.py](../server/routes/api_keys.py)):

0. **Emitir no nome de outro usuário exige `users.manage`** (403 `owner_must_be_self`). Sem isso, `apikey.manage` seria uma escalada silenciosa: bastaria cunhar uma chave no nome de um admin para herdar a instalação inteira — e o guardrail 1 não pega, porque quem escala *confirma*. A régua é `users.manage` porque quem cria e edita usuários já fabrica esse poder pela porta da frente, então para essa pessoa nada muda. A MESMA régua recorta a listagem (`GET /api/api-keys` fica preso ao ator, e o `?user_id=` não burla) e a revogação (chave alheia responde **404**, não 403 — um 403 confirmaria que ela existe).
   ⚠️ O seletor "Usuário dono" da tela é servido por **`GET /api/api-keys/owners`**, não por `GET /api/users`: aquele é gateado por `users.manage`, então quem tinha só `apikey.manage` tomava 403 e ficava com o seletor VAZIO, sem conseguir emitir nem para si mesmo. A rota devolve a lista já recortada + `can_choose_others`, e a tela só desenha — o recorte nunca é decisão do cliente.
1. **Chave para usuário `admin` exige `confirm: true` explícito** (409 `admin_owner_requires_confirm`) — é a única proteção que resta depois de tirar os escopos: chave de admin vazada = instalação inteira. Recusar de vez seria pior (há casos legítimos), mas nenhum deles é acidental.
2. `apikey.manage` fora de `ROLE_DEFAULTS` — ninguém ganha de brinde o direito de cunhar chave.
3. **Rate-limit com bucket PRÓPRIO por chave** (`state.api_key_calls`, 600 req/min por chave, 429). ⚠️ **Nunca** reaproveitar o bucket do login (uma integração legítima esgotaria o limite de um IP inteiro) e **nunca** derivá-lo de `audit_ip`, que é autodeclarado ⇒ forjável — ver [server/client_ip.py](../server/client_ip.py).
4. **`expires_at` preenchido por padrão** (1 ano); "sem validade" também exige `confirm`.

### Fachada `/api/v1` (a superfície estável)

Pacote [server/routes/v1/](../server/routes/v1/) — **fino de propósito**: traduz HTTP ↔ os serviços/repos que já existem e **não** reimplementa regra. DTO próprio (status HTTP com significado + corpo direto, erro `{"error": {code, message}}`), NÃO o envelope `{ok, data|error}` da UI — que é bom para o Preact e ruim para um integrador.

| Módulo | Cobre | Delega para |
|---|---|---|
| `v1/contacts.py` | listar/pesquisar (trigram), obter, criar, editar, excluir, etiquetas | `contact_repo` + [db/search/contact_search.py](../db/search/contact_search.py) + `contact_service` |
| `v1/messages.py` | enviar texto, **enviar mídia** (multipart e URL/base64), ler thread paginada/ancorada, buscar na conversa, marcar lida | **`MessagingService.send_text`** + **`MessagingService.send_media_upload`** + [db/search/message_search.py](../db/search/message_search.py) |
| `v1/conversations.py` | listar, filtrar (motor completo), contar, obter, resolver/reabrir, atribuir, IA, etiquetas | [db/filters/](../db/filters/) + `conversation_service` |
| `v1/catalog.py` | etiquetas (contato e conversa), atributos personalizados, canais/inboxes (**leitura**) | `tag_repo`, `custom_attribute_repo`, `channel_repo`, `inbox_repo` |

- **Não existe chave `message.send`** no catálogo RBAC: o envio gateia em **`conversation.reply`**, e atributos em **`custom_attribute.manage`** (não `settings.*`). D5 proíbe catálogo novo.
- `GET /api/v1/conversations/filter-schema` reexpõe `conv_filters.available_dimensions` — **o motor de filtros já se autodescreve**, então o integrador descobre as dimensões (inclusive os atributos personalizados DESTA instalação) sem documentação escrita à mão.
- `GET /api/v1/openapi.json` devolve o esquema **só das rotas `/api/v1`**, pronto para codegen. Fica sob `/api/*` de propósito: o middleware o protege, então uma chave válida o lê e um anônimo não mapeia a superfície. É a alternativa sempre disponível ao `/docs` global, que segue atrás de `WHATSBOT_ENABLE_DOCS` e exporia a API inteira do painel.
- **Etiqueta de conversa e atributo personalizado têm `PATCH`** (não só criar/apagar). Recriar NÃO é equivalente a editar: a identidade da etiqueta é o `id` (renomear preserva os vínculos com as conversas já etiquetadas) e a do atributo é `attribute_key` (os valores gravados nas entidades são indexados por ela). Por isso `attribute_key`/`type`/`applies_to` **não** se editam — mandá-los é ignorado, exceto num atributo `is_system`, onde renomear é 400 explícito. Toda escrita do registro de etiquetas faz broadcast de `conversation_labels_registry_changed`, senão a paleta do operador só vê a mudança depois de recarregar a tela.
- Administração (usuários, papéis, configuração, motor de IA, auditoria, plugins, **escrita** de canal) fica **fora** da v1 (D6).
- **Multicanal no envio**: o mesmo número pode ter conversa em várias caixas. O integrador escolhe por `conversation_id` (preferido) ou `channel_id`, e a resolução usa `get_open_for_contact_inbox` — **nunca** `get_open_for_contact`, que é contact-scoped e funde canais.

### ⚠️ `MessagingService.send_text` — por que a extração NÃO era opcional

Não existia função de serviço para enviar TEXTO: o envio do operador eram ~150 linhas dentro do handler `POST /api/contacts/{phone}/send`, carregando regras que **não podem ser duplicadas** — bloqueio da janela de 24h, `filter.reply.part` (cópia exibida) **e** `filter.outbound.text` (wire-only), `filter.conversation.before_reopen`, o JID real via `wire_target` (o ghost-send do 9º dígito), `@menções` de grupo, `state.recently_sent` chaveado no alvo de wire, `abort_ai_cycle` (calar o ciclo da IA em andamento — plano 96) e o desvio de sandbox. Uma segunda implementação em `/api/v1` mandaria mensagem fora da janela, para o JID errado, sem calar a IA — **e nada disso apareceria como erro**.

O caminho tem precedente no próprio repo: o refactor **R14** já unificou a cauda das três rotas de mídia em `MessagingService.send_media`. **R-txt** faz o mesmo para texto, e as DUAS rotas — painel e v1 — chamam a mesma função. Junto subiram para o módulo cinco resolvedores que eram closures de `contacts.register_routes` e por isso não existiam para mais ninguém: `is_sandbox_contact`, `resolve_channel_id`, `wire_target`, `resolve_inbox_id` e `session_window_block` (este devolve um **veredito de domínio**; a rota do painel volta a montar o `_err(409)`). As closures antigas continuam existindo como atalho e **delegam** ao serviço.

⚠️ **`inbox_guard` é um callable, não um flag.** O gate de inbox é chamado no MESMO ponto do handler original — depois do desvio de sandbox e antes de resolver o canal. **A ordem é contrato**: um contato de sandbox nunca passou pelo gate de inbox, e checá-lo antes mudaria o comportamento do painel.

A escrita de CONTATO seguiu o mesmo caminho: [app/services/contact_service.py](../app/services/contact_service.py) (`validate_custom_attributes` + `update_info` + `delete_contact`) preserva as duas tolerâncias que o handler tinha — atributo soft-deleted (P49) e chave herdada da migração Chatwoot são IGNORADAS, não recusadas, porque o painel reenvia o JSON inteiro no save e um 400 abortaria a gravação toda.

### Envio de mídia pela v1 — imagem, áudio, documento, vídeo (e imagem COMO documento)

Duas rotas, as duas gateadas em **`conversation.reply`** (não há permissão nova — D5) e as duas delegando a **`MessagingService.send_media_upload`**, a mesma função das quatro rotas de mídia do painel:

| Rota | Corpo | Para quem |
|---|---|---|
| `POST /api/v1/messages/media` | `multipart/form-data`: `file`, `phone`, `kind`, `caption?`, `filename?`, `conversation_id?`/`channel_id?` | **caminho primário** — quem tem os bytes em mãos (um Worker que acabou de gerar o PDF: `FormData` + `fetch`, sem infraestrutura extra) |
| `POST /api/v1/messages/media/link` | `application/json`: `phone`, `kind`, **`url` XOR `content_base64`**, `filename` (**obrigatório**), `caption?`, `content_type?`, `conversation_id?`/`channel_id?` | quem já tem o arquivo num endereço (CRM, Windmill, bucket público) |

Resposta (201): `{sent, msg_id, conversation_id, channel_id, kind, media_path, sandbox}`.

#### ⚠️ `kind` é do CHAMADOR e NUNCA é inferido do MIME

`kind` ∈ `image` · `audio` · `document` · `video`; valor fora disso é **400 `invalid_kind`**, nunca um palpite.

É essa decisão que entrega **"imagem como arquivo"**: um `.png` enviado com `kind=document` sai por `/send/file` do GOWA (`documentMessage`), que **não recomprime** — a foto chega com a qualidade original. O mesmo arquivo com `kind=image` é recomprimido pelo WhatsApp. Os dois caminhos existem de propósito, e é o integrador que escolhe:

```bash
# certificado do aluno, com a qualidade preservada
curl -X POST https://SEU-HOST/api/v1/messages/media \
  -H "X-Api-Key: wsk_live_xxxx.yyyy" \
  -F file=@certificado.pdf \
  -F phone=5511999999999 \
  -F kind=document \
  -F 'caption=Seu certificado do curso 🎓'
```

O painel funciona pela mesma regra: quem decide é o **gesto** (a zona "Foto ou vídeo" × a zona "Arquivo" do compositor — `classifyFile(file, sendMode)`), não o tipo do arquivo. Inferir o `kind` do `Content-Type` "por conveniência" mata o recurso e o faz em silêncio.

#### A tabela por-`kind` (uma só, em `_MEDIA_KIND_SPEC`)

| `kind` | conteúdo persistido / bolha | legenda repassada ao canal | `filename` no fio | transcreve |
|---|---|---|---|---|
| `image` | a legenda | ✅ | — | ✅ (se o canal marcou "Enviadas") |
| `audio` | `[Áudio]` | ❌ — é nota de voz (PTT), o protocolo não carrega legenda | — | ✅ |
| `document` | `[Documento enviado: <nome>]` + legenda | ✅ | ✅ o nome original | ❌ |
| `video` | a legenda, ou `[Vídeo]` | ✅ | — | ❌ |

Mandar `caption` com `kind=audio` é **400 `caption_not_supported`** — aceitar-e-descartar faria o integrador descobrir o problema pelo relato do cliente, não na primeira chamada.

⚠️ **`filename` define o tipo que o destinatário vê.** O MIME que vai ao provedor sai de `mimetypes.guess_type()` sobre esse nome, não do conteúdo; sem extensão o arquivo chega como `application/octet-stream` e não abre com duplo clique. Por isso ele é **obrigatório** na rota `/link` e recomendado na multipart (onde cai no nome da parte). O nome **em disco** é outro: reescrito a partir do MIME validado (`unique_media_name`), com extensão executável neutralizada para `.bin` — é a defesa contra XSS armazenado do plano 64.

#### A URL é buscada pelo servidor — e isso é SSRF por construção

`POST /media/link` com `url` faz o servidor abrir uma conexão escolhida pelo chamador. Sem guard, uma chave com apenas `conversation.reply` viraria scanner da rede interna e leitor do endpoint de metadados da nuvem. [app/services/remote_media.py](../app/services/remote_media.py) aplica seis regras, cada uma com teste próprio:

| # | Regra | Por quê |
|---|---|---|
| G1 | só `http`/`https` (**400 `bad_scheme`**) | `file://`/`gopher://` leem disco e falam protocolos internos |
| G2 | redirecionamento **não** é seguido (**400 `bad_status`**) | redirect é o bypass clássico: o alvo público responde `302` para `127.0.0.1` |
| G3 | recusa loopback, RFC1918, link-local, CGNAT, ULA IPv6 e `169.254.169.254` (**400 `blocked_host`**) | é o guard que impede a escalada |
| G4 | teto aplicado no **streaming** (**413 `too_big`**) | `Content-Length` é declarado pelo servidor remoto — mentir nele é trivial |
| G5 | timeout de 10 s | uma URL que pendura prende um worker |
| G6 | tudo vira erro de domínio | alvo inalcançável é entrada inválida (400), nunca bug do WhatsBot (500) |

⚠️ **G3 é sobre o IP, não sobre o nome.** `localhost.meudominio.com` é um host público registrado que resolve para `127.0.0.1`; recusar por substring não pega isso. O host é resolvido, **todos** os endereços são checados (registro duplo público+privado é recusado) e a conexão é feita contra o IP já aprovado, com `Host:` e SNI originais — o que também fecha a janela de DNS rebinding entre a checagem e o connect.

#### Teto de tamanho: 50 MB nos três caminhos

⚠️ **O teto de upload é por LISTA DE CAMINHOS** (`_UPLOAD_PATH_RE` em [server/upload_limits.py](../server/upload_limits.py)) — uma rota de upload nova que não entre nessa regex **não tem teto nenhum** e carrega o corpo inteiro para a RAM do processo. Acrescentar a rota lá é parte de shipá-la.

O caminho `content_base64` **não passa** por esse middleware (o corpo é JSON, não multipart), então tem teto próprio: `base64_exceeds`, medido no **comprimento da string**, antes de decodificar — decodificar para depois medir já é ter o arquivo inteiro na memória. As duas fronteiras coincidem: o integrador recusa no mesmo tamanho de arquivo, tenha escolhido a forma que tiver.

#### Erros que valem conhecer

| Status | `code` | Quando |
|---|---|---|
| 400 | `invalid_kind` · `caption_not_supported` · `empty_file` · `missing_field` · `conflicting_source` · `invalid_base64` | entrada malformada |
| 400 | `bad_scheme` · `blocked_host` · `bad_status` · `unreachable` | a URL do `/link` (G1–G6) |
| 403 | `forbidden` · `inbox_forbidden` | sem `conversation.reply`, ou a caixa de destino não é do dono da chave |
| 409 | `ambiguous_target` | o número tem conversa aberta em mais de uma caixa — informe `conversation_id` ou `channel_id` |
| 409 | `session_window_closed` | fora da janela de 24h num canal Meta |
| 413 / 415 | `too_big` · `bad_format` | o canal declara limite de tamanho/formato para aquele `kind` |

⚠️ **`media_path` é relativo a esta instância.** O armazenamento de mídia é per-instância por design; sem pasta persistente no deploy a mídia enviada vira 404 depois de um redeploy — ver o gotcha em [docs/OPERACAO.md](OPERACAO.md).

#### O refactor que tornou isso possível (R-media)

O envio de mídia já funcionava por API antes disto (as rotas do painel aceitam `X-Api-Key`); o que não existia era **na superfície versionada**, e o que existia estava **copiado quatro vezes**. `send_media` (R14) já unificava a cauda — send → persist → broadcast → `message.sent`; o que continuava duplicado era o **preparo**: sandbox, canal, tomada humana (`abort_ai_cycle`), alvo de wire, janela de 24h, limites do canal, gravação, transcode de áudio/vídeo, e a tabela de seis parâmetros por `kind`. **R-media** subiu tudo isso para `MessagingService.send_media_upload`; as quatro rotas do painel delegam e a v1 é a quinta chamadora, não a quinta cópia.

⚠️ **A ordem do preparo é contrato, e difere da de `send_text`**: em mídia o `inbox_guard` vem **antes** do desvio de sandbox (no texto vem depois). ⚠️ **As ordens de preparo de áudio e vídeo são diferentes de propósito** — áudio bloqueia barato *antes* de gravar quando o canal não declara `AudioLimits`; vídeo sempre grava antes porque precisa do `ffprobe`. "Harmonizar" as duas quebra o transcode de uma delas.

### Webhooks de saída (push)

Todo o resto da API é *pull*. Um CRM que precise saber "chegou mensagem" ou "conversa resolvida" teria de fazer polling — o único push era o `/ws`, que exige sessão de painel e não é escopado.

- **Núcleo no core, no MESMO barramento** ([server/webhook_dispatcher.py](../server/webhook_dispatcher.py)): um subscriber `*`, no molde do [server/audit_listener.py](../server/audit_listener.py). Ele só faz o barato — confere a allowlist e **enfileira** uma linha por endpoint; **nada de rede no caminho da request**. Quem POSTa, assina e re-agenda é o loop supervisionado `webhook_delivery` ([server/background.py](../server/background.py)).
- **Eventos de plugin viajam de graça**: plugin emite no mesmo barramento, então `protocolos`, `retornos` e companhia entregam eventos sem escrever transporte nenhum. Um plugin que precise de formato de terceiro implementa o seu e não passa por aqui.
- ⚠️ **`"*"` cobre apenas o conjunto CURADO** (`EXPORTABLE_EVENTS`), não "qualquer coisa do barramento". É o que impede que um endpoint cadastrado hoje comece a receber, num upgrade, um evento novo que ninguém revisou — e que `llm.after` (que leva o histórico da conversa e o prompt) ou `presence.changed`/`receipt.changed` (altíssimo volume) saiam da instalação por descuido. Evento de plugin precisa ser **nomeado**, direto ou por curinga (`protocolos.*`).
- **Assinatura**: `X-Whatsbot-Signature-256: sha256=HMAC_SHA256(segredo, corpo)` sobre os **bytes exatos** enviados — re-serializar o JSON do outro lado quebra a comparação (mesma armadilha do webhook da Meta). O segredo aparece uma vez e é **rotacionável** (`POST /api/webhooks/{id}/rotate-secret`), justamente porque não é recuperável.
- **Estado em TABELA, nunca em memória** (`webhook_endpoints` / `webhook_deliveries`): um toggle de plugin derruba o processo, e uma entrega pendente não pode morrer com ele. Retry com backoff 30s → 6h; esgotado, a entrega vira **dead-letter** e FICA na tabela (é o registro de que algo não chegou — só as `delivered` são expurgadas). 20 falhas seguidas desligam o endpoint automaticamente.
- **`raw` e segredos nunca saem no corpo**: o `raw` do provedor pode carregar base64 de mídia inteira, e as chaves de segredo são removidas recursivamente antes de enfileirar.
- ⚠️ **Não confundir com `/api/webhook/{provider}/{channel_id}`**, que é o webhook de **ENTRADA** (o provedor nos chamando). Aqui é o contrário.

### Onde as coisas ficam

| Peça | Arquivo |
|---|---|
| Lógica pura da chave (gerar/resolver/expirar) | [server/api_keys.py](../server/api_keys.py) |
| Persistência da chave | [db/repositories/api_key_repo.py](../db/repositories/api_key_repo.py) |
| Emitir/listar/revogar (painel) | [server/routes/api_keys.py](../server/routes/api_keys.py) |
| Fachada versionada | [server/routes/v1/](../server/routes/v1/) |
| Envio de texto compartilhado | `MessagingService.send_text` ([app/services/messaging_service.py](../app/services/messaging_service.py)) |
| Envio de mídia compartilhado (R-media) | `MessagingService.send_media_upload` + `_MEDIA_KIND_SPEC` ([app/services/messaging_service.py](../app/services/messaging_service.py)) |
| Busca SSRF-safe de URL (rota `/link`) | [app/services/remote_media.py](../app/services/remote_media.py) |
| Teto de upload (50 MB) | [server/upload_limits.py](../server/upload_limits.py) — `_UPLOAD_PATH_RE`, `base64_exceeds` |
| Escrita de contato compartilhada | [app/services/contact_service.py](../app/services/contact_service.py) |
| Webhooks de saída | [server/webhook_dispatcher.py](../server/webhook_dispatcher.py) + [db/repositories/webhook_repo.py](../db/repositories/webhook_repo.py) + [server/routes/webhooks_out.py](../server/routes/webhooks_out.py) |
| Tela (abas Chaves de API / Webhooks) | [web/static/js/components/IntegrationsScreen.js](../web/static/js/components/IntegrationsScreen.js) — rota `/api-keys` |
| Migrações | `0064_api_keys`, `0065_outbound_webhooks` |
