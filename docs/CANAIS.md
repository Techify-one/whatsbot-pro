# Canais — provider plugável, identidade, JID, proxy e limites

> Guia do contrato de canal (provider) do core. O [`CLAUDE.md`](../CLAUDE.md) carrega a **regra curta** e os avisos ⚠️;
> aqui está o **porquê**, o histórico e o detalhe. Texto migrado do `CLAUDE.md` no plano 139
> — nada foi reescrito na migração, só realocado.

Canais da Meta (Messenger/Instagram) têm guia próprio: [CANAIS_META.md](CANAIS_META.md).
**Regra que atravessa tudo aqui: o provider DECLARA, o core AVALIA** — não existe
`if provider ==` em nenhuma superfície do core.

---

### Filtro de tipos de JID (canal GOWA)

O tipo de um chat do WhatsApp é definido pelo **sufixo do JID** (depois do `@`), não pelo número — o prefixo `120363…` é compartilhado por grupo, canal e comunidade. [channels/jid.py](../channels/jid.py) (`classify_jid`) mapeia o sufixo para um tipo lógico: `person` (`@s.whatsapp.net`), `person_lid` (`@lid`), `group` (`@g.us`), `newsletter` (Canal, `@newsletter`), `broadcast` (Status/transmissão, `@broadcast`), `bot` (`@bot`), `unknown`.

No webhook GOWA ([server/routes/webhook.py](../server/routes/webhook.py)), logo após resolver o `chat_jid`, a mensagem é classificada e **descartada antes de materializar qualquer contato** se o tipo não estiver na lista permitida — corrige o bug em que tudo que não era `@g.us` caía no ramo "pessoa" (um post de Canal virava "contato fantasma"). A lista permitida vem de `config.allowed_jid_types` do canal GOWA (lida do canal `default`, que é single-channel no inbound; cache de 30s, invalidado ao editar a config do canal). Tipos `unknown` nunca são bloqueados (preserva comportamento legado).

⚠️ **São DOIS defaults diferentes, em arquivos diferentes** (plano 103) — não "conserte" um achando que é o outro:

| | Default de **CRIAÇÃO** | Fallback de **RUNTIME** |
|---|---|---|
| Constante | `GOWA_DEFAULT_JID_TYPES` — [channels/providers/gowa_channel.py:63](../channels/providers/gowa_channel.py#L63) | `DEFAULT_ALLOWED_JID_TYPES` — [channels/jid.py:38](../channels/jid.py#L38) |
| Valor | `person` + `person_lid` (**sem `group`**) | `person` + `person_lid` + `group` |
| Quando vale | semeia o formulário de um canal **novo** (via `config_fields[].default` do descriptor) | quando o canal **não tem** a chave salva (ou salvou lixo) |
| Alcance | só canal criado dali pra frente | **todo canal legado** sem a chave |

Canal GOWA novo nasce, portanto, **sem grupo marcado**: um número de atendimento individual materializava todo grupo de que participa (foram 118 contatos no incidente do plano 102). A opção continua visível e a um clique. Mexer no fallback de runtime seria **retroativo** — calaria grupos em canais antigos. A UI fica na criação/edição do canal GOWA em [web/static/js/components/ChannelsManager.js](../web/static/js/components/ChannelsManager.js) (`JidTypePicker`) — o usuário escolhe pelos rótulos amigáveis, sem ver o JID. Vale **apenas para canais GOWA**.

## Contrato de identidade de conta / dedup de canais (plano 32)

Dois canais **do mesmo provider** não podem apontar para a **mesma conta** (o mesmo número no GOWA, o mesmo `phone_number_id` na Cloud API, o mesmo bot no Telegram). A prevenção é **na origem** (bloqueio, não aviso) e a arquitetura é **genérica no core, fina no provider** — igual ao precedente `required_credentials`: o **plugin declara a identidade**, o **core faz todo o dedup** (comparação, storage, índice único, enforcement). Adicionar um provider novo (Instagram, Messenger, widget…) = implementar 1–2 métodos; **o core não muda** e **nunca tem `if provider ==`**.

- **Contrato** ([channels/base.py](../channels/base.py)): `AccountIdentity(kind, value)` é a chave de dedup — `kind` = namespace (`phone`/`phone_number_id`/`bot_id`/…), `value` = forma **canônica** não-vazia. Três ganchos no `Channel` (todos default no-op, então `test`/providers que não aderem simplesmente não deduplicam):
  - `identity_from_credentials(creds)` (classmethod) — identidade conhecível **no create** (está na credencial: Cloud `phone_number_id`, Telegram `bot_token`→`bot_id`). O core chama no create/update → **409** antes de persistir.
  - `account_identity()` (instância) — identidade que só aparece **pós-conexão** (GOWA `own_phone` pós-QR). O sweep chama e, num conflito, recusa.
  - `reject_duplicate()` — desfaz a conexão duplicada (default: `logout()`/`stop()`). O provider pode sobrescrever.
  Um provider pode implementar os dois, mas **com `kind` consistente** (Telegram usa `bot_id` nos dois — derivado do token `{bot_id}:{hash}`, sem rede — pra deduplicarem entre si, plano 32 P1).
- **Motor** ([channels/dedup.py](../channels/dedup.py)): `same(a, b)` (igualdade exata de `kind`+`value`; `None`/`""` nunca casa) e `find_conflict(provider, identity, exclude_channel_id)` (varre `channel_repo.list_all()` — só `enabled=1`/`archived=0`, mesmo provider). Puro de rede (só DB).
- **Storage** ([db/tables.py](../db/tables.py) + migration `0038`): colunas `channels.account_identity` + `account_identity_kind` e o índice único parcial `ux_channels_account_identity (provider, account_identity) WHERE enabled=1 AND archived=0 AND account_identity IS NOT NULL AND <> ''` — cinto de segurança de banco (serializa 2 QRs simultâneos: o 2º leva `IntegrityError`).
- **Enforcement** ([app/services/channel_service.py](../app/services/channel_service.py)): create/update resolvem `identity_from_credentials` e, num conflito, levantam `DuplicateChannelError` → **409** (update escapa a própria row via `exclude_channel_id` e checa as creds **efetivas** = armazenadas + edição, barrando editar-pra-colidir).
- **Sweep pós-conexão** ([app/services/channel_identity.py](../app/services/channel_identity.py) + loop `channel_identity_sweep_loop` em [server/background.py](../server/background.py), owner = plugin `gowa`): por canal vivo, lê `status()`+`account_identity()`, grava `own_phone`/`connected`/`logged_in`/`account_identity` **só quando muda**, e num conflito recusa via `reject_duplicate()` + `last_error` + `logged_in=0` (mantém `enabled=1` — não-destrutivo). Efeito colateral bom: persistir `own_phone` destrava o roteamento inbound `by_phone` (antes coluna morta).
- **Regras**: mesma conta em **providers diferentes** NÃO é duplicata (canais separados — plano 11 D1/D2); arquivados/desabilitados **não** contam; identidade GOWA usa `get_own_number` **device-scoped** (plano 32 F1 — nunca o número de outro device) e canônico BR (12↔13 dígitos colapsam numa forma). Implementar um provider novo: só leia [channels/base.py](../channels/base.py) + esta seção (ver `whatsapp_cloud`/`telegram`/`gowa` como exemplos).

## Provider de canal (plugin) — canais 100% plugáveis (plano 33)

Canais são **plugins de 1ª classe**: cada provider **se autodescreve** e as superfícies de oferta/renderização do core não o conhecem por nome — não há `if provider ==` no formulário, no pós-criação, nos chips/filtros das telas (catálogo único, plano 76), no card (slot `channel.card.rows`) nem no mascaramento de credencial. Ainda existem seams de compatibilidade específicos do GOWA fora dessas superfícies; removê-los depende do plano 100 F2. Adicionar os demais providers = shipar um plugin cuja subclasse de `Channel` implementa os hooks necessários, sem alterar a UI/form de Canais. Só **GOWA** vem auto-instalado; telegram/whatsapp_cloud/facebook_messenger/instagram/website são **importáveis** pelos ZIPs publicados no repositório `whatsbot-pro-plugins`.

- **Descriptor** ([channels/base.py](../channels/base.py) `provider_descriptor()`, classmethod): a fonte única do que o core precisa pra **oferecer + renderizar** o provider. Forma: `{provider, label, color, credential_fields:[{key,label,type,required,placeholder?,help?}], config_fields:[{key,label,type,options?,default?,...}], capabilities:{needs_qr,templates}, ai_sequential_default, contact_type, post_create, form_component}` (`contact_type` = o tipo que o canal marca nos contatos — garantido pelo `channel_service` mesmo se o provider sobrescrever o descriptor sem re-adicionar a chave; ver "Tipo de contato por canal"). Tipos de campo que o form genérico entende: `text`, `secret`, `token_suggest` (input + botão "Sugerir"), `multiselect` (checkbox group sobre `options`, seed de `default`), `generated` (read-only auto-preenchido por `prefix`). O default da base deriva um descriptor mínimo; os providers sobrescrevem ([gowa_channel.py](../channels/providers/gowa_channel.py), `telegram`, `whatsapp_cloud`, `facebook_messenger`, `instagram`, `website`). O JID-type catalog (que era hardcoded no frontend) agora é um `multiselect` no descriptor do GOWA — o provider é dono dele.
- **Endpoint** ([channel_service.py](../app/services/channel_service.py) `providers()` + `provider_descriptor(deps, p)`): `GET /api/channels/providers` devolve `{providers:[descriptor,...], required_credentials:{provider:[key,...]}}` só dos providers **registrados** (plugin ativo). Há dois contratos deliberadamente separados: `credential_fields[].required` valida a **criação nova**; o mapa `required_credentials`, derivado de `ChannelCapabilities.required_credentials`, descreve a **saúde operacional** de rows existentes e alimenta o aviso anti-zombie do card. O serviço garante que todo requisito operacional também apareça como obrigatório no descriptor, mas um provider pode apertar apenas a criação durante uma migração (Cloud exige `app_secret` novo sem chamar o legado fail-open de desconectado). Oferta = instalado; `ALLOWED_PROVIDERS` deixou de ser o gate (sobra só como allow-list de compat no create). Criar canal em `server/routes/channels.py` valida `provider ∈ (registrados ∪ ALLOWED_PROVIDERS)` e os campos obrigatórios do descriptor.
- **Mascaramento de credencial derivado do descriptor (plano 76 · H4/V9)** ([channel_service.py](../app/services/channel_service.py) `serialize`/`_public_cred_keys`): na borda da API, credencial sai em CLARO **só** se o provider a declarou `type: "text"` (identificador público — ex.: `phone_number_id`, `waba_id`, `page_id`), o resto é mascarado (`••••` + últimos 4). Sem a antiga lista `NON_SECRET_CRED_KEYS`. **Guarda de nome obrigatória**: mesmo `type:text`, uma chave cujo nome case `/(token|secret|password|senha|key)/i` é mascarada (+ WARNING) — um plugin que erre o `type` não vira vazamento. Default (provider não registrado / descriptor quebrado) = tudo mascarado.
- **Frontend genérico** ([web/static/js/components/channels/](../web/static/js/components/channels/)): `constants.js` tem os builders **puros** `buildCreatePayload`/`buildEditPayload` (montam credentials/config a partir do descriptor + valores coletados, sem branch de provider), `providerMeta`/`tintForColor` (badge por `color`), `initialConfigValues`, `missingCredsFor`, `buildEmbedSnippet` (interpolação PURA do `post_create.snippet_template`). `DescriptorFields.js` renderiza `CredentialFields`/`ConfigFields`/`MultiSelect` por `type`, e `FormComponentLoader` importa um `form_component` opcional via `import()` (seam pra provider rico; nenhum built-in usa). `ChannelForm`/`ChannelEditForm` são inteiramente dirigidos pelo descriptor. Testes puros: [constants.test.js](../web/static/js/components/channels/constants.test.js) (`node --test`).
- **Catálogo único de providers no cliente (plano 76 · H1)** ([web/static/js/services/providerCatalog.js](../web/static/js/services/providerCatalog.js)): a FONTE ÚNICA de "rótulo, cor, tint, bolinha e tipo de contato" de um provider fora da tela Canais. Faz UM fetch de `GET /api/channels/providers`, cacheia, e expõe `providerLabel/Color/Tint/Dot(p)`, `channelPickerMeta(p)`, `contactTypeFor(p)`, `contactTypeColorTokens()`, `fetchedProviders()`/`requiredCredentials()` (usados pelo `ChannelsManager` — sem fetch próprio) e `subscribe()`. Fallback estático mínimo (`gowa`/`test`) até o fetch chegar; provider desconhecido degrada para o próprio id em cinza (D3). Componentes re-renderizam via o hook [useProviderCatalog.js](../web/static/js/hooks/useProviderCatalog.js). **Substituiu os 5 mapas estáticos** (ChannelChip `CHANNEL_META`, ConversationInfoPanel `PROVIDER_LABELS`, ChannelPickerModal/NewConversationModal `PROVIDER_META`, contactTypes `CONTACT_TYPE_META` — este virou base curada + descoberta do catálogo). Regra: **nenhuma tela do core mapeia nome de provider → rótulo/cor**; tudo vem do descriptor.
- **Pós-criação dirigido pelo descriptor** ([ChannelsManager.js](../web/static/js/components/ChannelsManager.js)): `capabilities.needs_qr` → abre o QR ([QRConnect.js](../web/static/js/components/channels/QRConnect.js), genérico); `post_create.kind == "webhook_url"` → `WebhookNotice` com a URL de callback (`post_create.path` com `{channel_id}` substituído); `post_create.kind == "autoconfigure"` → POST em `post_create.endpoint` (`providerPostCreateAction`) e `AutoconfigureNotice` com o resultado (fallback long-poll via `webhook_path`); `post_create.kind == "embed_snippet"` → `EmbedSnippetNotice` com o snippet montado do `post_create.snippet_template` (o core interpola `{base_url}`/`{token}`; a chave do token vem de `token_config_key` — o core não conhece o path `/plugins/website/`). As ações de sessão do card (Conectar/Reconectar/Desconectar) são gated por `needs_qr`, não por nome. As flags de deep-link de modal são `?connect|?webhook|?autoconfig` (capability/post_create, nunca nome de provider).
- **Slot `channel.card.rows` (plano 76 · H2)** ([registry.js](../web/static/js/plugins/registry.js)): ponto de extensão aditivo no corpo do card de canal ([ChannelCard.js](../web/static/js/components/channels/ChannelCard.js), ctx `{channel, descriptor}`). O provider injeta a própria linha via `frontend_extends` — o `whatsapp_cloud` registra aqui o `WebhookHealthRow` (que vive no PLUGIN, em `static/` dentro do próprio `whatsapp_cloud`, e filtra por `ctx.channel.provider` internamente); usa o `http` de `buildPluginHttp` (o core não chama mais endpoint de plugin). Vazio ⇒ card byte-idêntico; desabilitar o plugin some a linha sem erro.
- **Bundling** ([plugins/bootstrap.py](../plugins/bootstrap.py) `BUNDLED_AUTO_INSTALL = ("gowa",)`): fresh install copia **só GOWA**, e `assets/plugin_examples/` contém **apenas o `gowa`** — a fonte, os testes e o ZIP dos demais vivem no repositório `whatsbot-pro-plugins` e chegam ao usuário pela loja de plugins (`Importar (.zip)`). Instalações existentes em `storages/plugins/` ficam intactas.
- **Config do provider mora no plugin**: status/config específicos (ex: webhook vs long-poll do Telegram) vivem na screen `config:true` do próprio plugin (`/telegram/config`), NÃO no form de edição do core — o core edita só nome + campos do descriptor + IA + agentes.
- **Comando**: `/new-channel` ([.claude/commands/new-channel.md](../.claude/commands/new-channel.md)) gera um provider correto por construção — subclasse `Channel` + capabilities + ganchos de identidade (plano 32) + `provider_descriptor()` + `contact_type()` (tipo do contato, ver "Tipo de contato por canal") + `entry.channels` + stubs `status`/`send`/`parse_inbound` (+ `lifecycle`/`routes`/`form_component` quando aplicável), sem tocar no core.

## Proxy de saída por número (plano 52)

Cada **canal GOWA** pode rotear a conexão do WhatsApp por um proxy de saída próprio (1 IP por número — ex.: IPs dedicados do webshare.io). O campo "Proxy de saída (opcional)" fica no form do canal (credencial `proxy_url`, tipo `secret` — mascarada na borda da API; formatos `socks5://user:pass@ip:porta` ou `http(s)://…`). **Arquitetura híbrida**: canal SEM proxy segue no processo GOWA compartilhado (inalterado); canal COM proxy ganha um **processo GOWA dedicado** — porta própria (persistida em `config.gowa_dedicated_port`), `cwd` próprio em `storages/gowa_ch_<id>/` (isola `whatsapp.db`/`chatstorage.db`; um symlink `statics` aponta de volta pra raiz pra mídia continuar servível), env **`WHATSAPP_PROXY`** (nunca argv — o cmd é logado/visível em `ps`) e webhook próprio em `/api/webhook/gowa/<id>`. O canal `default` (singleton legado) nunca é dedicado.

- **Orquestração** ([storages/plugins/gowa/processes.py](../assets/plugin_examples/gowa/processes.py), fonte em `assets/`): reconcile loop declarativo (task `gowa:process_reconcile`, ~15s) — `plan_reconcile` (puro) diffa desejado×rodando e aplica spawn/stop/restart; auto-cura claims órfãos (proxy removido com o servidor desligado). Transições: **ligar** proxy = evict do device no processo compartilhado (`logout` + `DELETE /devices/{id}`) ANTES do spawn dedicado → **re-parear por QR** (esperado, avisado no help do campo); **desligar** = para o processo, limpa porta/`gowa_isolation`, volta ao compartilhado (novo QR). `storages/gowa_ch_<id>/` é preservado ao desligar (a sessão sobrevive a um re-enable). Proxy inválido/proxy no `default` ⇒ `last_error` no canal, processo não sobe.
- **`channels.gowa_isolation`** (`shared|dedicated_process`, coluna da migration 0011) é atualizada pelo reconcile — observabilidade; a fonte de decisão é a credencial `proxy_url`.
- **Upgrade do plugin bundled** (P7): `bootstrap_gowa_upgrade` ([plugins/bootstrap.py](../plugins/bootstrap.py)) é **version-aware** — quando a versão do `plugin.yaml` bundled em `assets/` é MAIOR que a instalada em `storages/plugins/gowa`, o boot substitui a cópia instalada (swap atômico via temp+rename; tombstone de uninstall respeitado; nunca re-habilita plugin desabilitado; instalado mais NOVO que o bundled é deixado em paz). Edições manuais na cópia instalada são perdidas no bump (logado alto).
- **Recomendação de proxy**: IP **fixo e dedicado** por número (datacenter dedicado ou static residential) — NUNCA endpoint rotativo (IP muda por conexão = padrão de ban).
- ⚠️ **O campo bloqueia o autofill do navegador (plano 104)** — todo campo `type: "secret"` de canal (não só o proxy: `bot_token`, `access_token`, `app_secret`, `hmac_token`…) é `<input type="password">`, e o gerenciador de senha injetava nele a **senha do painel** por heurística (não é preciso haver `<form>`); o operador salvava sem olhar e o número parava — pior, um valor que *parecesse* URL subia processo dedicado e exigia QR novo. `secretInputProps(key)` ([constants.js](../web/static/js/components/channels/constants.js), aplicado em [DescriptorFields.js](../web/static/js/components/channels/DescriptorFields.js)) carrega **`autocomplete="new-password"`** — o `off` é **IGNORADO** pelo Chrome em campo de senha, então não "limpe" isso achando que é resquício — mais `name` estável **sem** as palavras `password`/`senha`/`token`/`secret` (a chave crua as contém e reativaria a heurística) e os opt-outs `data-lpignore`/`data-1p-ignore`/`data-bwignore`/`data-form-type`. Ao lado do input há um botão de mostrar/ocultar (começa sempre oculto, nunca persistido).
- **Formato recusado no save** (plano 104 F3, defesa em profundidade): o provider declara `credential_fields[].pattern` + `pattern_error` no descriptor e o core **só avalia** — `validateCredentials` no formulário ([constants.js](../web/static/js/components/channels/constants.js)) e `credential_format_errors` na criação/edição ([channel_service.py](../app/services/channel_service.py) → 400 nas rotas), regex **ancorada** e **case-insensitive** nos dois lados, sem `if provider ==`. Valor vazio e o placeholder `••••` nunca são validados (na edição vazio = "manter a atual", então row legada fora do formato continua editável), regex quebrada passa (fail-open) e a mensagem cita **o campo, nunca o valor** (seria a senha do operador em log). O `pattern` do `proxy_url` ([gowa_channel.py](../channels/providers/gowa_channel.py)) espelha o `validate_proxy_url` do plugin — que **continua** sendo a rede final pós-save para rows legadas. ⚠️ **Sem cobertura de teste** (verificado no plano 139): o `CLAUDE.md` afirmava estar "travado por `tests/integration/test_channel_credential_pattern.py`" — esse arquivo **não existe**, e `node --test web/static/js/components/channels/constants.test.js` roda, mas não exercita `validateCredentials` nem o `pattern`. As duas metades (`channel_service.credential_format_errors` e o `validateCredentials` do formulário) estão implementadas e sem rede: escrever o teste é pendência aberta.

## Limites de mídia por canal (anexo incompatível é bloqueado, não falha)

Anexo (imagem/áudio/documento/vídeo) que não atende às regras do canal é **bloqueado no compositor com um popup** antes do envio — em vez de virar uma bolha "falhou" depois que o provedor recusa. Mesmo padrão policy-vs-mechanism dos outros ganchos: **o provider declara os números, o core só avalia**, sem `if provider ==`.

- **Contrato** ([channels/base.py](../channels/base.py)): `MediaLimits(max_bytes, extensions)` — o irmão genérico de `VideoLimits` (que ainda acrescenta regras de codec). Declarados em `ChannelCapabilities.media_limits` como `{kind: limits}` (`image`/`audio`/`document`/`video`/`sticker`). Kind sem declaração = nunca bloqueia (GOWA/Telegram).
- **Números da Meta moram no plugin** (`whatsapp_cloud/channels.py` `_MEDIA_LIMITS`): imagem 5 MB JPEG/PNG · áudio 16 MB AAC/AMR/MP3/M4A/OGG · vídeo 16 MB MP4/3GP H.264+AAC · documento 100 MB PDF/TXT/DOC(X)/XLS(X)/PPT(X) · figurinha 500 KB WebP. Import defensivo (core antigo sem `MediaLimits` continua carregando o plugin).
- **Core** ([channels/media_limits.py](../channels/media_limits.py)): `limits_for(caps, kind)`, `validate_upload(filename, size, caps, kind)` → `MediaVerdict(reason ∈ ok/too_big/bad_format, message PT-BR)` e `describe(caps, video_transcode_available=…)` → o dict JSON que vai pro painel. O fallback legado de VÍDEO segue em [channels/video_validate.py](../channels/video_validate.py) (plugins anteriores ao plano 65).
- **Backend**: as rotas `/send-image`, `/send-audio` e `/send-document` chamam `_media_limits_block` **antes de gravar o upload** (413 `too_big` / 415 `bad_format`, sem arquivo órfão); `/send-video` mantém o caminho próprio (valida codec → recomprime com ffmpeg → só então bloqueia). O payload de conversa/contato carrega `media_limits` ao lado de `revoke_supported`/`edit_supported`.
- **Painel**: [web/static/js/services/mediaLimits.js](../web/static/js/services/mediaLimits.js) (`checkMediaFile`, puro, espelha o backend; testes `node --test`) roda na SELEÇÃO do arquivo; recusado ⇒ [MediaRejectedModal.js](../web/static/js/components/contacts/MediaRejectedModal.js) e o anexo nem entra na fila. Vídeo com `transcode: true` (ffmpeg presente no servidor) NÃO é bloqueado no cliente — o servidor recomprime; sobra só o teto de entrada de 200 MB. Recusa que só aparece no servidor (codec) remove a bolha otimista e abre o mesmo popup. Sandbox e nota privada não são validados (não saem para o provedor).

## Tipo de contato por canal (plano tipos-de-contato)

Cada contato registra o **tipo herdado do canal que o materializou**, gravado em `contacts.contact_type` (migration 0050, `server_default='outros'`; rows legadas foram backfilladas para `whatsapp` porque antecedem o Telegram). O **provider declara** o tipo, o **core grava e exibe** — mesmo padrão genérico dos outros hooks de canal (nenhum `if provider ==` no core).

- **Contrato** ([channels/base.py](../channels/base.py)): `Channel.contact_type()` (classmethod, default `"outros"`). GOWA ([gowa_channel.py](../channels/providers/gowa_channel.py)) e WhatsApp Cloud (`whatsapp_cloud/channels.py`) retornam `"whatsapp"` (mesmo tipo); Telegram (`telegram/channels.py`) retorna `"telegram"` (não guarda telefone — o `phone` é o chat_id numérico do Telegram). O descriptor (`provider_descriptor`) também expõe `contact_type`.
- **Gravação**: só no INSERT do contato. `ContactMemory._resolve_contact_type()` ([agent/memory.py](../agent/memory.py)) resolve a classe do provider do canal (via `_resolve_provider_class`, mesmo helper do source_id) e passa a `contact_repo.get_or_create(..., contact_type=...)`. Fail-open para `"outros"` quando o provider não resolve (registry não cabeado em testes, canal sem provider). Um contato já existente **não** é re-tipado (o tipo é do 1º canal que o criou).
- **Exibição**: marca (chip colorido) abaixo do nome/telefone no painel do contato ([ContactInfoPanel.js](../web/static/js/components/contacts/ContactInfoPanel.js)) e em cada linha da tela Contatos ([ContactsListScreen.js](../web/static/js/components/ContactsListScreen.js)). O catálogo de rótulo/cor por tipo mora em [web/static/js/services/contactTypes.js](../web/static/js/services/contactTypes.js) (`contactTypeMeta`), tolerante a tipos novos/desconhecidos.
- **Filtro**: dimensão `contact_type` ("Tipo de contato", multi-select eq/ne) nos dois construtores de filtro — [ConversationFilterDialog.js](../web/static/js/components/contacts/ConversationFilterDialog.js) (hub de atendimentos) e [ContactFilterDialog.js](../web/static/js/components/contacts/ContactFilterDialog.js) (tela Contatos). Avaliação client-side via `clauseMatches` ([conversationRows.js](../web/static/js/services/conversationRows.js)) sobre as rows já carregadas (o campo `contact_type` vem no payload de `list_contacts` e no detalhe).
- **Provider novo**: implemente `contact_type()` (ver `/new-channel`); sem override os contatos herdam `"outros"`.


## O `ts` do inbound: por que ele é coagido em três camadas (plano 141)

**Incidente (2026-08-18 → 2026-08-25, produção).** O operador relatou "tem uma
conversa que abriu porém não tem nada nela". Não era uma conversa: era **todo o
inbound 1:1 dos canais GOWA** sendo destruído havia 6 dias.

O plano 129 passou a persistir o timestamp REAL do provedor
(`ts = event.ts or None`) — desenho certo, guard incompleto: protegia contra
**ausente/zero**, nunca contra **tipo**. Dos cinco providers, quatro coagiam o
valor num `_to_float()` local. O quinto — o GOWA, o único que mora no core —
repassava cru, e é o único que manda **string RFC 3339** (`"2026-08-24T17:43:58Z"`)
em vez de epoch: 1.447 de 1.447 webhooks capturados na mesma forma.

A cadeia da destruição:

| # | Passo | Estado do `ts` |
|---|---|---|
| 1 | `gowa/inbound.py` monta o `InboundEvent` | **str** — sem coerção |
| 2 | A dataclass aceita calada (`ts: float` é anotação, não enforcement) | **str** |
| 3 | O item entra na fila do batch | **str** |
| 4 | O batch é **consumido** (`pending_messages.pop`) **antes** do save | item já saiu da memória |
| 5 | `message_repo.add`: `ts = ts or time.time()` | **str não-vazia é truthy ⇒ PASSA** |
| 6 | INSERT em `messages.ts` (`Float`) | 💥 `InvalidTextRepresentation` |
| 7 | Exceção engolida no orquestrador → só `executions.error` | **mensagem perdida** |

⚠️ **O passo 5 é a armadilha central.** `ts = ts or time.time()` *parece* um guard
de tipo e não é.

⚠️ **O passo 4 é o que torna a falha irrecuperável.** O `pop` antes do save é
intencional (plano 33 F6), mas significa que qualquer exceção depois dele destrói
o item — não há retry possível sem duplicar mensagem.

**O que mascarou por 6 dias:** os canais GOWA têm `allowed_jid_types` sem
`group` (default de criação desde o plano 103), e ~99% do tráfego deles é grupo —
descartado no portão de JID **antes** do ponto de crash. O portão não é o bug: é
o anestésico. Marcar `group` teria transformado 1 mensagem/semana em centenas/dia.

### A correção

1. **Parser** — `_epoch(value)` em [gowa/inbound.py](../gowa/inbound.py) entende
   epoch numérico, string numérica e RFC 3339; nunca levanta; ininterpretável
   vira `0.0`, que a cadeia a jusante já traduz em `time.time()`.
2. **Contrato** — `InboundEvent.__post_init__` ([channels/events.py](../channels/events.py))
   força `ts` a float. Fecha a **classe**, não só este payload — inclusive para
   provider de plugin de terceiro, que o core não revisa.
3. **Repositório** — `message_repo.add` tenta `float(ts)` e, falhando, loga
   `warning` e carimba `time.time()`. **Falha de carimbo nunca custa a mensagem.**

⚠️ **O fallback é `time.time()`, nunca `0.0`.** Gravar `0.0` põe a mensagem em
1970 e ela afunda para sempre no topo do fio (a thread ordena por `(ts, id)`).

⚠️ **Fuso.** `datetime.fromisoformat` devolve objeto **naive** para uma string sem
`Z`/offset, e `.timestamp()` de um naive assume **hora local** — em BRT isso
desloca o carimbo em 3h. O helper carimba o naive como UTC de propósito. É a
mesma armadilha que já mordeu na migração dos agendamentos de retorno.

**Efeito visível a plugins:** `parsed_msg["ts"]` em `filter.message.before_save`
passa de `str` para `float` nos canais GOWA. É correção, não regressão — todo
consumidor já esperava número.

Rede: `tests/integration/test_inbound_provider_ts_ordering.py` (os quatro testes
do plano 141 falham sem a correção e passam com ela).
