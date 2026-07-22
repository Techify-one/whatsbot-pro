# Plano 73 — Criador de templates Meta completo (botões + cabeçalho de mídia) no canal WhatsApp Cloud

> **Status:** PLANEJAMENTO · **Data:** 2026-07-21 · **Escopo:** médio-grande (core: 6 camadas + 1 endpoint novo + frontend; provider plugin whatsapp_cloud; sem migration de DB — nova credencial mora em `channels.account`/credenciais do canal)
> **Origem:** pedido do usuário (Empresa Exemplo prod). O criador de templates atual do WhatsBot (modal "Novo template") só cria **cabeçalho de texto + corpo + rodapé** — não faz **botões** nem **cabeçalho de mídia** (imagem/vídeo/documento). O usuário tinha uma automação no Chatwoot (pasta [enviar-template-meta/](../enviar-template-meta/), PHP+JS) que abria uma tela para criar templates "de várias formas". Este plano porta as capacidades de criação daquela referência para o criador nativo do WhatsBot.
> **Método:** verificado nesta sessão — (a) leitura integral do criador de referência (`enviar-template-meta/js/modules/template-creator.js`, `api/create-template.php`, `api/upload-example.php`, `api/fetch-media-id.php`, `index.php`, `config/whatsapp.php`) com as formas de payload/API exatas; (b) leitura integral da fatia vertical do WhatsBot (frontend → api → 2 rotas → service → outbound → base → provider) com `arquivo:linha`; (c) workflow de 5 sub-agentes que cruzou referência × atual e confirmou que **só** `OutboundRouter.create_template` é passthrough `**kwargs` — todas as outras camadas usam parâmetros explícitos e precisam ser alargadas. Nada de memória.
> **Forma da solução:** alargar o contrato de criação de template em toda a pilha para carregar **botões** (todos os tipos de call-to-action) e **cabeçalho de mídia** (via handle de upload resumável), adicionar uma credencial **`app_id`** ao canal Cloud, um **endpoint novo de upload de exemplo** (arquivo → Meta → `handle`), e estender o formulário `CreateTemplateForm` (radio de formato de cabeçalho + dropzone de mídia + repetidor de botões). O provider (plugin `whatsapp_cloud`) passa a montar os componentes Graph de mídia/botões e a injetar o TTL de UTILITY.
>
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| **D1** ✅ (2026-07-21) | **Botões = escopo estendido**: QUICK_REPLY (resposta rápida) + URL (fixo e dinâmico) + PHONE_NUMBER (ligar) + COPY_CODE (copiar código) | O provider monta os 4 tipos; o form tem um repetidor com `<select>` de tipo e campos por tipo. **NÃO** entram FLOW, CATALOG/multi-produto nem carrossel (nicho; a referência não fazia) |
| **D2** ✅ (2026-07-21) | **Cabeçalho de mídia via upload resumável** (arquivo → Meta), **não** URL pública | Exige nova credencial **`app_id`** no canal Cloud (hoje inexistente). O exemplo do cabeçalho vira `example.header_handle: ["4:…"]`. **Descarta** o caminho `header_url` (que na referência era campo morto — ver §6 "Falsos positivos") |
| **D3** ✅ (implícita, princípio do repo) | Solução **capability-gated, sem `if provider ==`** | Todo o alargamento passa por `Channel`/`OutboundRouter`; o core nunca cita "whatsapp_cloud" por nome. Só o provider (plugin) conhece a forma Graph |
| **D4** ✅ (2026-07-21) | Nada em produção depende deste criador ainda ⇒ **alargar o contrato de forma agressiva** (widen-the-pipe), sem stopgap paralelo | Uma única assinatura estendida de `create_template` em todas as camadas; sem duas rotas concorrentes |

**Fora de escopo (adiado — ver P5–P7):** preview rico estilo bolha do WhatsApp, categoria AUTHENTICATION (OTP), analytics/Custos de template (a Meta tem `template_analytics`/`pricing_analytics`; o WhatsBot já tem seu próprio custo por LLM — não confundir), carrossel e Flows.

---

## 1. Resumo executivo

O criador nativo do WhatsBot é uma pilha de **7 camadas** que hoje só sabe montar `HEADER(TEXT) + BODY + FOOTER` com exemplos de `{{n}}`. A tela ([TemplatePicker.js → `CreateTemplateForm`, L435-584](../web/static/js/components/contacts/TemplatePicker.js#L435-L584)) diz explicitamente no comentário (L434): *"Media headers / buttons are intentionally out of scope here."* A referência do Chatwoot criava, além disso, **cabeçalho de imagem/vídeo/documento** (via upload resumável → `header_handle`) e **botões** (na referência só QUICK_REPLY, mas as formas Graph de URL/PHONE/COPY_CODE estão documentadas e o **lado de envio** do WhatsBot já as entende — [TemplatePicker.js:180-205](../web/static/js/components/contacts/TemplatePicker.js#L180-L205)).

A solução: **alargar `create_template` na pilha inteira** para aceitar `header_format` + `header_handle` (mídia) e `buttons`, adicionar a credencial **`app_id`**, um **endpoint de upload de exemplo** que transforma um arquivo no `handle` que a Meta exige, e estender o formulário. Só **1 das 7 camadas** já é passthrough ([outbound.py:134](../channels/outbound.py#L134)); as outras 6 têm parâmetros explícitos e precisam ser tocadas. Sem migration de DB (a credencial `app_id` usa o mesmo armazenamento de credenciais de canal das demais). Como o provider é um **plugin import-only** (`whatsapp_cloud`), a entrega da parte do provider é **via zip/cópia para `storages/plugins/`** — ver §9 (risco de entrega).

---

## 2. Como funciona hoje (mapa da pilha)

A UI + endpoints de template são **core**, capability-gated por `supports(channel_id, "templates")` — **nunca** por nome de provider. O provider (plugin `whatsapp_cloud`) monta a forma Graph.

| # | Camada | Arquivo:linha | O que faz hoje | Precisa alargar? |
|---|--------|---------------|----------------|:---:|
| 1 | Form (frontend) | [TemplatePicker.js `CreateTemplateForm` L435-584](../web/static/js/components/contacts/TemplatePicker.js#L435-L584) | Campos: Nome, Categoria (só UTILITY/MARKETING), Idioma (datalist `pt_BR,en_US,en,es_ES,es`), Cabeçalho **texto** + exemplos, Corpo + exemplos, Rodapé. **Sem botões, sem mídia.** L434 declara fora de escopo | **SIM** |
| 2 | API client (frontend) | [api.js `createConversationTemplate` L657](../web/static/js/services/api.js#L657), [`createChannelTemplate` L688](../web/static/js/services/api.js#L688) | POST do payload simples (8 campos) | **SIM** (+ helper de upload) |
| 3 | Rota conv-scoped | [conversations.py `POST /api/atendimentos/{conv_id}/templates` L787-835](../server/routes/conversations.py#L787-L835) | `body.get(...)` explícito dos 8 campos; valida nome/categoria/exemplos; gate `template.create` | **SIM** |
| 4 | Rota channel-scoped | [channels.py `POST /api/channels/{channel_id}/templates` L153-190](../server/routes/channels.py#L153-L190) | Mesma extração explícita | **SIM** |
| 5 | Service | [template_service.py `create_template` L110-127](../app/services/template_service.py#L110-L127) | kwargs explícitos → `outbound.create_template` | **SIM** |
| 6 | Router | [outbound.py `create_template` L134-152](../channels/outbound.py#L134-L152) | `inst.create_template(name, **kwargs)` — **único passthrough** | **NÃO** ✅ |
| 7 | Contrato base | [base.py `create_template` L348-359](../channels/base.py#L348-L359) | Assinatura explícita, `raise NotImplementedError` | **SIM** |
| 8 | Provider (plugin) | [whatsapp_cloud/channels.py `create_template` L511-568](../assets/plugin_examples/whatsapp_cloud/channels.py#L511-L568) | Monta `HEADER(TEXT)`+`BODY`+`FOOTER`; auth **Bearer**; `POST /{waba_id}/message_templates`. **Sem BUTTONS, sem mídia, sem `message_send_ttl_seconds`** | **SIM** |

**Credenciais do canal Cloud hoje** ([provider_descriptor L90-…](../assets/plugin_examples/whatsapp_cloud/channels.py#L90), `credential_fields` L99): `access_token`, `phone_number_id`, `waba_id`, `verify_token`. **Não há `app_id`** — necessário para o upload resumável (`POST /{app_id}/uploads`). Helpers do provider já prontos para reuso: `_cred(key)` [L141], `_access_token` [L164], `_base_url()` [L167], `_headers()` [L170], `_graph_error()` [L835], `_invalidate_templates_cache()` [L508].

⚠️ **Gotcha 1 (entrega do provider):** `WhatsAppCloudChannel` roda a partir de `storages/plugins/whatsapp_cloud/channels.py` (import-only, não é auto-instalado — só `gowa` é). A fonte versionada é `assets/plugin_examples/whatsapp_cloud/`. Editar a fonte **não** atualiza a cópia instalada — a entrega é por **re-import do zip** (`assets/channel_plugins/whatsapp_cloud-plugin.zip`) ou cópia manual. As camadas 1-7 são **core** e sobem com o app; só a camada 8 (provider) é plugin-delivered.

⚠️ **Gotcha 2 (fluxo de mídia):** a referência tem **3 fluxos de mídia distintos** que NÃO se misturam — só o **resumável** (`/{app_id}/uploads`) participa da **criação**:

| Fluxo | Referência | Endpoint Meta | Produz | Usado para |
|-------|-----------|---------------|--------|-----------|
| **Upload resumável** (App) | `upload-example.php` | `POST /{app_id}/uploads` → `POST /{upload_session_id}` | **handle** `"4:…"` | `HEADER.example.header_handle` na **CRIAÇÃO** ← **este plano** |
| Media upload (phone) | `upload-media.php` | `POST /{phone_number_id}/media` | `media_id` | **envio** de msg com mídia (runtime) |
| Media upload de URL | `fetch-media-id.php` | baixa URL → `POST /{phone_number_id}/media` | `media_id` | idem, fonte = URL remota |

⚠️ **Gotcha 3 (auth do upload):** o passo 2 do resumável usa `Authorization: OAuth {token}` (**não** `Bearer`) + header `file_offset: 0` + `Content-Type: {mime}`, corpo = bytes crus (não multipart). O passo 1 (criar sessão) na referência põe `access_token` na **query string** — este plano **evita** isso e passa por header (regra do repo: sem segredo na URL — ver P1).

---

## 3. O que a referência realmente cria (alvo verificado)

Formas Graph exatas (verificadas em `create-template.php` + `template-creator.js buildPayload` + `upload-example.php`). **Estas são as formas que o provider do WhatsBot passará a montar.**

**Corpo do POST de criação** — `POST /{version}/{waba_id}/message_templates`, header `Authorization: Bearer {token}`, `Content-Type: application/json`:

```jsonc
{
  "name": "confirmacao_pedido",          // ^[a-z0-9_]+$
  "category": "UTILITY",                  // UTILITY | MARKETING (AUTH fora de escopo)
  "language": "pt_BR",                    // WhatsBot já é multi-idioma (melhor que a ref, que fixa pt_BR)
  "message_send_ttl_seconds": 43200,      // ⚠️ injetar SÓ quando category == UTILITY (12h)
  "components": [
    { "type": "HEADER", "format": "IMAGE",
      "example": { "header_handle": ["4:n0pFaAxHJB...=="] } },   // mídia = handle-only
    { "type": "BODY", "text": "Olá {{1}}, seu pedido {{2}} foi confirmado.",
      "example": { "body_text": [["Maria", "#12345"]] } },       // array-de-arrays (1 linha)
    { "type": "FOOTER", "text": "Empresa Exemplo" },                // sem variáveis
    { "type": "BUTTONS", "buttons": [ /* ver abaixo */ ] }
  ]
}
```

**Formas de botão (D1 — todos os 4 tipos):**

```jsonc
{ "type": "QUICK_REPLY",  "text": "Falar com atendente" }
{ "type": "URL",          "text": "Ver site", "url": "https://exemplo.com.br" }               // URL fixa
{ "type": "URL",          "text": "Rastrear", "url": "https://rb.com/t/{{1}}",
                          "example": ["https://rb.com/t/12345"] }                                  // URL dinâmica (1 var no fim)
{ "type": "PHONE_NUMBER", "text": "Ligar agora", "phone_number": "+5511999999999" }
{ "type": "COPY_CODE",    "example": "PROMO2026" }                                                 // sem "text"
```

**Fluxo do upload resumável (`upload-example.php`, alvo do endpoint novo):**

```
STEP 1  POST /{version}/{app_id}/uploads?file_name=..&file_length=..&file_type=..
        (corpo vazio; header Authorization: OAuth {token}   ← ver P1, sem token na URL)
        → { "id": "upload:MTph..." }
STEP 2  POST /{version}/{upload_session_id}
        headers: Authorization: OAuth {token}, file_offset: 0, Content-Type: {mime}
        corpo = bytes crus do arquivo
        → { "h": "4:n0pFaAxHJB...==" }     ← este é o header_handle
```

**Limites/validações a espelhar** (Meta; alguns "a confirmar" — ver P9):

| Campo | Limite | Fonte |
|-------|--------|-------|
| Nome | `^[a-z0-9_]+$`, minúsculo | ref + core já valida ([conversations.py:800](../server/routes/conversations.py#L800)) |
| Cabeçalho texto | ≤ 60 chars, ≤ 1 variável | ref `[ui §3b]` |
| Corpo | ≤ 1024 chars, obrigatório, vars sequenciais de 1 | ref `[ui §4]` |
| Rodapé | ≤ 60 chars, sem variáveis | ref `[ui §5]` |
| Botão (texto) | ≤ 25 chars | ref `[ui §6]` |
| Botões | ≤ 10 no total; **≤ 2 URL, ≤ 1 PHONE_NUMBER, ≤ 1 COPY_CODE**; quick-replies agrupados | Meta (a confirmar — P9) |
| Mídia | JPG/PNG, MP4/3GPP, PDF/DOC/DOCX/XLS/XLSX; **≤ 16 MiB** | ref `[backend §3.1]` |

---

## 4. Inventário / análise (itens a fazer, por camada)

| Item | Camada | Onde | O que falta | Risco | Esforço |
|------|--------|------|-------------|:-----:|:------:|
| Credencial `app_id` | provider descriptor | [channels.py provider_descriptor L90, credential_fields L99](../assets/plugin_examples/whatsapp_cloud/channels.py#L99) | Adicionar campo `{key:"app_id", type:"text", required:False, help:"…"}`; ler via `_cred("app_id")` | baixo | S |
| Método `upload_example()` | provider | channels.py (novo, perto de L508) | Resumável 2-passos (OAuth); recebe bytes+mime+filename → `{ok, handle, error}` | médio | M |
| `create_template` estendido | provider | [channels.py L511-568](../assets/plugin_examples/whatsapp_cloud/channels.py#L511-L568) | Montar HEADER de mídia (`header_handle`), BUTTONS (4 tipos), injetar `message_send_ttl_seconds` p/ UTILITY | médio | M |
| Assinatura base | contrato | [base.py L348-359](../channels/base.py#L348-L359) | Adicionar `header_format`, `header_handle`, `buttons` aos kwargs | baixo | S |
| Service estendido | service | [template_service.py L110-127](../app/services/template_service.py#L110-L127) | Repassar campos novos; + função `upload_template_example(deps, channel_id, ...)` | baixo | S |
| Rota conv POST | rota | [conversations.py L787-835](../server/routes/conversations.py#L787-L835) | Extrair+validar `header_format`/`header_handle`/`buttons`; passar adiante | médio | M |
| Rota channel POST | rota | [channels.py L153-190](../server/routes/channels.py#L153-L190) | Idem | médio | M |
| Endpoint upload (×2) | rota | conversations.py + channels.py (novos) | `POST …/templates/upload-example` multipart → valida MIME/16MiB → provider → `{handle}` | médio | M |
| Router: capability de upload | router | [outbound.py](../channels/outbound.py#L134) | Método `upload_template_example(channel_id, ...)` (mesmo padrão de `create_template`) | baixo | S |
| API client | frontend | [api.js L657/L688](../web/static/js/services/api.js#L657) | `uploadTemplateExample(...)`; estender payload de create | baixo | S |
| Form estendido | frontend | [TemplatePicker.js L435-584](../web/static/js/components/contacts/TemplatePicker.js#L435-L584) | Radio de formato de cabeçalho; dropzone+upload de mídia (guarda handle); repetidor de botões com validação de mistura; montar payload | médio | L |
| Testes | testes | [tests/test_endpoints.py](../tests/test_endpoints.py) | Create com botões/mídia (provider mockado), endpoint de upload, erros de validação | baixo | M |

### Outras coisas que a referência tinha (avaliadas)

| Feature da referência | Decisão | Motivo |
|-----------------------|---------|--------|
| Listagem com busca + filtros (status/categoria/tipo) + paginação | **já existe** parcialmente no WhatsBot ([TemplatePicker lista L296-351](../web/static/js/components/contacts/TemplatePicker.js#L296-L351)) + delete | Melhoria opcional (P8), não bloqueante |
| Preview estilo bolha WhatsApp | **adiado** (F5) | Nice-to-have; o form já tem prévia textual do corpo |
| Analytics/Custos de template | **fora de escopo** (P7) | Épico à parte (endpoints `template_analytics`/`pricing_analytics` da Meta) |
| Idioma fixo `pt_BR` | **não portar** | WhatsBot já é multi-idioma (datalist) — regressão evitada |

---

## 5. Mudanças de infraestrutura (habilitadores)

1. **Credencial `app_id` no descriptor do provider** — sem migration: credenciais de canal já têm armazenamento genérico (`channel_credential_repo`). Só adicionar o campo ao `credential_fields` — por ser dirigido pelo descriptor, ele aparece **tanto no cadastro** (`ChannelForm`) **quanto na edição** (`ChannelEditForm`) do canal, sem tocar em UI de canal — e lê-lo com `_cred("app_id")`. O `/info` do plugin ([routes.py:204-210](../assets/plugin_examples/whatsapp_cloud/routes.py#L204)) também pode listar `app_id` em `credential_keys` (informativo).
2. **Alargar o contrato `create_template`** — a assinatura passa a ser (ilustrativa, não implementar aqui):
   ```python
   def create_template(self, name, *, category, language, body_text,
                        header_text=None, footer_text=None,
                        body_examples=None, header_examples=None,
                        header_format=None, header_handle=None,   # NOVO (mídia)
                        buttons=None) -> dict: ...                 # NOVO (lista de dicts tipados)
   ```
   `buttons` = lista já-tipada vinda do frontend (`[{type, text?, url?, phone_number?, example?}, …]`); o provider a converte para a forma Graph e valida shape mínima. `header_format ∈ {IMAGE,VIDEO,DOCUMENT}` + `header_handle` ⇒ HEADER de mídia; senão `header_text` ⇒ HEADER de texto (comportamento atual).
3. **Método de upload no router** — `OutboundRouter.upload_template_example(channel_id, file_bytes, mime, filename)` seguindo o mesmo padrão capability-gated de `create_template` ([outbound.py:134](../channels/outbound.py#L134)); default no `base.py` levanta/`{ok:False}` para providers sem suporte.

---

## 6. Falsos positivos descartados

| Suspeita | Veredito | Razão |
|----------|----------|-------|
| "Adicionar criação de template no `routes.py` do plugin" | **NÃO** | O plugin `routes.py` só tem webhook-health ([routes.py:295-301](../assets/plugin_examples/whatsapp_cloud/routes.py#L295-L301) diz explicitamente que criação/listagem/envio vivem no **core**). Mexer lá seria arquitetura errada |
| "O lado de envio precisa mudar para suportar mídia/botões" | **NÃO** | `TemplatePicker.buildComponents` ([L180-205](../web/static/js/components/contacts/TemplatePicker.js#L180-L205)) já monta parâmetros de header de mídia (link) e de botão URL dinâmico no **envio**. O gap é só na **criação** |
| "Implementar o fallback `header_url` (URL pública) da referência" | **NÃO** (D2) | Na referência esse campo é **morto**: `buildPayload` nunca o lê; a criação de mídia é **handle-only**. Além disso D2 escolheu resumável |
| "`OutboundRouter.create_template` precisa alargar" | **NÃO** | É `**kwargs` passthrough ([outbound.py:134](../channels/outbound.py#L134)) — os campos novos passam sozinhos |
| "Precisa migration para `app_id`" | **NÃO** | Credenciais de canal usam armazenamento genérico; sem coluna nova |
| "Adicionar categoria AUTHENTICATION agora" | **NÃO** (P6) | O core já aceita AUTH em `_TEMPLATE_CATEGORIES` ([conversations.py:785](../server/routes/conversations.py#L785)), mas templates de autenticação são um fluxo OTP especial (botões/expiração próprios) — nicho fora de escopo |

---

## 7. Fases / Roadmap

### Diagrama de dependências (waves)

```
WAVE 0 (paralelos — contrato/skeleton, sem I/O entre si)
  F0  credencial app_id (provider)            🟢
  F1  upload_example() no provider            🟢  [runtime depende de: F0]
  F2  widen do contrato create (7 camadas)    🟢  [independe do upload]
  F3  api.js: helpers (upload + payload)       🟢
        │                    │            │
        │ (F1 → F4)          │ (F2 → F5)  │ (F3 → F4,F5)
WAVE 1
  F4  endpoint de upload de exemplo (×2 rotas) 🔴  [depende de: F1, F3]
  F5  frontend: form estendido                 🔴  [depende de: F2, F4, F3]
        │
WAVE 2
  F6  testes (endpoints + validação)           🔴  [depende de: F2,F4,F5]
  F7  polish: preview + contadores (opcional)  🟢  [depende de: F5]
```

### Tabela de fases

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|:----:|------|-----------|:-----:|:-----:|---------------|
| 0 | **F0** | provider — credencial `app_id` | 🟢 | baixo | Form de canal Cloud mostra "App ID"; `_cred("app_id")` retorna o valor salvo |
| 0 | **F1** | provider — `upload_example()` resumável | 🟢 [depende runtime: F0] | médio | Chamada com bytes de teste retorna `handle` (ou erro claro) |
| 0 | **F2** | contrato — widen em base/provider/service/2 rotas | 🟢 | médio | Criar template com `buttons`+`header_handle` (provider mockado) monta os componentes Graph certos |
| 0 | **F3** | frontend — `api.js` helpers | 🟢 | baixo | `uploadTemplateExample` e create estendido existem e tipam o payload |
| 1 | **F4** | rota — `POST …/templates/upload-example` (conv + channel) | 🔴 [depende: F1,F3] | médio | `curl` multipart de 1 imagem retorna `{handle}`; MIME/tamanho inválidos → 400 |
| 1 | **F5** | frontend — `CreateTemplateForm` estendido | 🔴 [depende: F2,F4,F3] | médio | Criar template com cabeçalho de imagem + 3 botões (1 URL dinâmica, 1 telefone, 1 quick reply) end-to-end → PENDING na Meta |
| 2 | **F6** | testes | 🔴 [depende: F2,F4,F5] | baixo | `tests/test_endpoints.py` verde no Postgres cobrindo create+upload+erros |
| 2 | **F7** | polish (preview/contadores) — opcional | 🟢 [depende: F5] | baixo | Contadores de caractere + prévia mostram limites; modo escuro legível |

---

### Fase F0 — Credencial `app_id` no provider Cloud (aparece no **cadastro** e na edição do canal)
**Objetivo:** o canal Cloud passa a ter um campo **"App ID"** já **na hora de cadastrar** o canal (e também na edição), para o upload resumável de mídia. Como o form de canal é **100% dirigido pelo descriptor** (`ChannelForm`/`ChannelEditForm` renderizam `credential_fields` via `DescriptorFields`/`CredentialFields` — CLAUDE.md §"Frontend genérico"), basta declarar o campo no descriptor: ele surge **automaticamente nos dois fluxos** (criar e editar), sem tocar em componente de UI de canal.
**Itens:**
- `[sequencial]` Em [channels.py provider_descriptor `credential_fields` L99](../assets/plugin_examples/whatsapp_cloud/channels.py#L99), adicionar `{"key":"app_id","label":"App ID (Meta)","type":"text","required":False,"placeholder":"ID do App na Meta","help":"Necessário só para criar templates com cabeçalho de mídia (imagem/vídeo/documento). Em developers.facebook.com → seu App → Configurações → ID do aplicativo."}`.
  - **Explícito (pedido do usuário):** por vir do `credential_fields`, o campo aparece **no formulário de CRIAÇÃO do canal** (`ChannelForm`) — não só na edição — então já "fica lá" no cadastro. Nada a mudar no core do form: o `buildCreatePayload`/`buildEditPayload` ([constants.js](../web/static/js/components/channels/constants.js)) montam as credenciais a partir do descriptor, incluindo o `app_id`, sem branch de provider.
  - `required:False` de propósito: um canal Cloud que não vá criar templates de mídia continua podendo ser cadastrado sem `app_id`.
- `[paralelo]` Opcional: incluir `app_id` na lista `credential_keys` do `/info` ([routes.py:204](../assets/plugin_examples/whatsapp_cloud/routes.py#L204)) — informativo.
- `[sequencial]` Confirmar que `_cred("app_id")` ([L141](../assets/plugin_examples/whatsapp_cloud/channels.py#L141)) lê corretamente (via `registry.get_credential`).
**Pronto quando:** (1) abrir **"Novo canal" → WhatsApp Cloud** mostra o campo "App ID" no formulário de cadastro; (2) editar um canal Cloud existente também mostra e persiste o valor; (3) reler devolve o valor salvo; (4) sem `app_id` a criação de mídia falha com erro acionável (não 500).

#### Status de execução — Fase F0
**Estado:** ✅ Concluída (2026-07-21)
- **O que foi feito:** Credencial `app_id` adicionada ao `credential_fields` do `provider_descriptor` do WhatsApp Cloud (`assets/plugin_examples/whatsapp_cloud/channels.py`), `required:False`, com `help` apontando para developers.facebook.com. Também incluída em `credential_keys` do `/info` (`routes.py`). `plugin.yaml` 1.1.0 → 1.2.0.
- **Como foi feito / decisões:** Sem tocar em UI de canal: por vir do descriptor, o campo aparece sozinho no ChannelForm (cadastro) e no ChannelEditForm (edição), e `buildCreatePayload`/`buildEditPayload` já o carregam. Leitura via `_cred("app_id")`.
- **Problemas / pendências:** Nenhuma.
- **Verificação:** Payload Graph montado com o provider carregado por `importlib` e `httpx` stubado; `_cred("app_id")` alimenta a URL `…/{app_id}/uploads`.

---

### Fase F1 — `upload_example()` no provider (upload resumável)
**Objetivo:** transformar bytes de um arquivo no `header_handle` que a Meta exige na criação.
**Itens:**
- `[sequencial]` Novo método em `WhatsAppCloudChannel` (perto de [L508](../assets/plugin_examples/whatsapp_cloud/channels.py#L508)): `upload_example(self, file_bytes: bytes, mime: str, filename: str) -> dict` retornando `{ok, handle?, error?}`.
- Passo 1: `POST {_base_url()}/{app_id}/uploads` com params `file_name/file_length/file_type` **e** `Authorization: OAuth {token}` no header (P1 — não pôr token na query). Ler `id` da resposta.
- Passo 2: `POST {_base_url()}/{id}` com headers `Authorization: OAuth {token}`, `file_offset: 0`, `Content-Type: {mime}`, `content=file_bytes` (httpx `content=`, não `files=`). Ler `h` da resposta.
- `[sequencial]` Guard: sem `app_id`/`token` → `{ok:False, error:"missing_app_id"}`. Normalização de MIME de vídeo (`quicktime/mpeg/x-msvideo → video/mp4`) espelhando `upload-example.php:51-60`.
- `[paralelo]` Espelhar defaults do provider: `_base_url()` já injeta a versão Graph; usar `_graph_error(resp)` para mensagem humana.
- `[sequencial]` No contrato base ([base.py](../channels/base.py#L348)) adicionar `upload_example(...)` default `{"ok":False,"error":"not_supported"}`; no router ([outbound.py:134](../channels/outbound.py#L134)) adicionar `upload_template_example(channel_id, ...)` capability-gated (`supports("templates")`).
**Pronto quando:** teste unitário/manual com bytes de um PNG pequeno e `app_id` de teste retorna um `handle` `"4:…"`; sem `app_id` retorna erro claro.

#### Status de execução — Fase F1
**Estado:** ✅ Concluída (2026-07-21)
- **O que foi feito:** Novo `WhatsAppCloudChannel.upload_example(file_bytes, mime, filename)` (2 passos do upload resumável) + `_normalize_upload_mime`. Contrato base `Channel.upload_example` com default `{ok:False, error:'not_supported'}` e `OutboundRouter.upload_template_example(channel_id, …)` capability-gated por `templates`.
- **Como foi feito / decisões:** P1 respeitado: `Authorization: OAuth {token}` por HEADER nos dois passos (nunca token na query). Passo 2 usa `content=` (bytes crus) + `file_offset: 0` + `Content-Type: {mime}`. MIME de vídeo normalizado (quicktime/mpeg/x-msvideo → video/mp4). Sem `app_id` → erro PT-BR acionável, nunca 500.
- **Problemas / pendências:** A aceitação do header no passo 1 pela Meta ainda não foi exercitada contra a API real (só com cliente stubado) — validar no 1º upload real.
- **Verificação:** Script com `httpx` stubado: as 2 chamadas saem para `…/v21.0/A/uploads` e `…/v21.0/upload:S1` com os headers certos e devolvem `{ok:True, handle:'4:HANDLE=='}`.

---

### Fase F2 — Widen do contrato de criação (7 camadas)
**Objetivo:** os campos `header_format`, `header_handle` e `buttons` atravessam a pilha até o provider montar os componentes.
**Itens (ordem: de dentro pra fora, um commit por camada):**
- `[sequencial]` **Provider** [channels.py:511-568](../assets/plugin_examples/whatsapp_cloud/channels.py#L511-L568): estender assinatura (ver §5.2). Montar:
  - HEADER de mídia quando `header_format ∈ {IMAGE,VIDEO,DOCUMENT}` e `header_handle` presente → `{"type":"HEADER","format":header_format,"example":{"header_handle":[header_handle]}}` (senão mantém o ramo de texto atual).
  - BUTTONS: converter cada `buttons[i]` para a forma Graph por tipo (QUICK_REPLY/URL/PHONE_NUMBER/COPY_CODE — §3); anexar `{"type":"BUTTONS","buttons":[…]}` quando não-vazio.
  - Injetar `message_send_ttl_seconds: 43200` no payload quando `category.upper()=="UTILITY"` (hoje **não** faz — [L546-551](../assets/plugin_examples/whatsapp_cloud/channels.py#L546-L551)).
- `[sequencial]` **Base** [base.py:348-359](../channels/base.py#L348-L359): adicionar os kwargs novos à assinatura (docstring).
- `[sequencial]` **Service** [template_service.py:110-127](../app/services/template_service.py#L110-L127): repassar `header_format`/`header_handle`/`buttons` ao `outbound.create_template`.
- `[paralelo]` **Rota conv** [conversations.py:787-835](../server/routes/conversations.py#L787-L835) e **Rota channel** [channels.py:153-190](../server/routes/channels.py#L153-L190): extrair+validar os campos novos do `body` (formato de cabeçalho ∈ conjunto válido; `buttons` = lista de dicts; contagem/mistura básica — ver P9) e passar adiante. Manter os gates `template.create`.
- **Router** [outbound.py:134](../channels/outbound.py#L134): **nenhuma mudança** (passthrough).
**Pronto quando:** com o provider mockado, um POST de create com `header_format:"IMAGE"`+`header_handle:"4:x"`+`buttons:[…]` produz o `components` Graph esperado (asserção no teste F6); o caminho legado (só texto) continua **byte-idêntico**.

#### Status de execução — Fase F2
**Estado:** ✅ Concluída (2026-07-21)
- **O que foi feito:** `header_format`, `header_handle` e `buttons` atravessam base → service → 2 rotas → provider. O provider monta HEADER de mídia (`example.header_handle`), o componente BUTTONS (QUICK_REPLY/URL/PHONE_NUMBER/COPY_CODE) e injeta `message_send_ttl_seconds: 43200` só em UTILITY.
- **Como foi feito / decisões:** A validação virou função pura compartilhada no service (`normalize_header_media`, `normalize_buttons`) para as duas rotas não divergirem — limites: ≤10 botões, ≤2 URL, ≤1 PHONE_NUMBER, ≤1 COPY_CODE, texto ≤25, URL com `{{n}}` exige exemplo. A conversão para a forma Graph ficou no provider (`_graph_buttons`), core sem nenhum `if provider ==`. `OutboundRouter.create_template` não mudou (passthrough).
- **Problemas / pendências:** Nenhuma. Os limites de mistura são otimistas de propósito: a Meta continua sendo a validação final e seu `error_user_msg` é repassado.
- **Verificação:** Testes de endpoint com provider fake: componentes/kwargs certos, e um teste de regressão garante que o caminho só-texto continua sem mídia/botões.

---

### Fase F3 — API client (frontend)
**Objetivo:** o frontend tem função para subir o exemplo e um payload de create estendido.
**Itens:**
- `[paralelo]` Em [api.js](../web/static/js/services/api.js#L657) adicionar `uploadConversationTemplateExample(convId, file)` e `uploadChannelTemplateExample(channelId, file)` (multipart `FormData`, POST no endpoint de F4).
- `[paralelo]` Estender `createConversationTemplate`/`createChannelTemplate` para carregar `header_format`, `header_handle`, `buttons` (backward-compat: campos opcionais).
**Pronto quando:** os helpers existem e montam `FormData`/JSON corretos (revisão de código); nada quebra nos envios atuais.

#### Status de execução — Fase F3
**Estado:** ✅ Concluída (2026-07-21)
- **O que foi feito:** `uploadConversationTemplateExample(convId, file)` e `uploadChannelTemplateExample(channelId, file)` em `api.js` (via `uploadRequest`, multipart), e os comentários de `create*Template` documentando os campos novos.
- **Como foi feito / decisões:** Reuso do `uploadRequest` do `httpClient.js` (mesmo tratamento de 401 dos outros uploads) em vez de um `fetch` próprio. Campos novos são opcionais no payload → chamadas existentes intactas.
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `node --check` em `api.js`; exercitado de ponta a ponta pelos testes de endpoint dos dois endpoints de upload.

---

### Fase F4 — Endpoint de upload de exemplo (conv + channel)
**Objetivo:** um arquivo enviado pelo operador vira um `handle` da Meta.
**Itens:**
- `[paralelo]` `POST /api/atendimentos/{conv_id}/templates/upload-example` ([perto de conversations.py:837](../server/routes/conversations.py#L837)) e `POST /api/channels/{channel_id}/templates/upload-example` ([perto de channels.py:192](../server/routes/channels.py#L192)): `multipart/form-data` campo `file`.
- Gate `template.create`; `supports(channel_id,"templates")`; senão 400/403.
- Validar MIME (whitelist da §3: jpeg/png/mp4/3gpp/pdf/doc/docx/xls/xlsx) e tamanho ≤ 16 MiB **antes** de chamar a Meta → 400 com mensagem clara.
- `await asyncio.to_thread(outbound.upload_template_example, channel_id, file_bytes, mime, filename)`; devolver `{ok, handle}` ou `{ok:False, error}`.
- Ler os bytes uma vez (`await file.read()`), respeitando o cap (não estourar RAM — o cap de 16 MiB protege).
**Pronto quando:** `curl -F file=@foto.png` no endpoint retorna `{ok:true, data:{handle:"4:…"}}`; arquivo de tipo/ tamanho inválido → 400; canal sem `app_id` → erro acionável.

#### Status de execução — Fase F4
**Estado:** ✅ Concluída (2026-07-21)
- **O que foi feito:** `POST /api/atendimentos/{conv_id}/templates/upload-example` e `POST /api/channels/{channel_id}/templates/upload-example` (multipart, campo `file`), gated por `template.create` + `supports(channel_id,'templates')`.
- **Como foi feito / decisões:** MIME (whitelist JPG/PNG/MP4/3GPP/PDF/DOC(X)/XLS(X)) e tamanho (≤16 MiB) validados por `tpl_svc.validate_example_upload` ANTES de chamar o provider; nada é gravado em disco (bytes vão direto ao provider via `asyncio.to_thread`). Falha do provider → 502 com a mensagem da Meta.
- **Problemas / pendências:** O cap de 16 MiB é aplicado depois do `file.read()` (a request já foi recebida) — protege a Meta e o disco, não a RAM do request.
- **Verificação:** Testes: 200 + handle, MIME fora da whitelist → 400, >16 MiB → 400, canal sem templates → 400, conversa/canal inexistente → 404.

---

### Fase F5 — Frontend: `CreateTemplateForm` estendido
**Objetivo:** o operador cria templates com cabeçalho de mídia e botões pela tela nativa.
**Itens:**
- `[sequencial]` **Radio de formato de cabeçalho** (Nenhum/Texto/Imagem/Vídeo/Documento) — quando Texto, mantém o input+exemplos atual; quando mídia, mostra **dropzone/seletor de arquivo**.
- `[sequencial]` **Upload de mídia:** ao selecionar arquivo, chamar `upload*TemplateExample` (F3/F4), guardar o `handle` no estado, mostrar status ("Enviando…", "Pronto ✓", erro) e uma **prévia** (thumb para imagem; chip `[Vídeo]/[Documento]`). Bloquear "Criar" enquanto o upload não terminar.
- `[sequencial]` **Repetidor de botões** ("+ Adicionar botão"): cada linha = `<select>` de tipo (Resposta rápida/Link/Telefone/Copiar código) + campos condicionais (texto; url + exemplo se `{{1}}` no fim; telefone; código de exemplo). Validar limites/mistura (≤10; ≤2 URL; ≤1 telefone; ≤1 código; texto ≤25) — bloquear submit com mensagem.
- `[sequencial]` Montar o payload com `header_format`/`header_handle`/`buttons` e enviar via create estendido.
- `[paralelo]` **Modo escuro:** todos os controles novos com `wa-*`/`.wa-field` (radio, select, dropzone, chips) — testar com tema escuro ligado.
**Pronto quando:** criar um template UTILITY com cabeçalho de imagem + corpo com 2 vars + rodapé + botões [URL dinâmica, telefone, quick reply] resulta em template **PENDING** na Meta (verificável na listagem com badge "Pendente"); recarregar a lista mostra o novo template.

#### Status de execução — Fase F5
**Estado:** ✅ Concluída (2026-07-21)
- **O que foi feito:** `CreateTemplateForm` estendido: radio de formato de cabeçalho (Nenhum/Texto/Imagem/Vídeo/Documento), seletor de arquivo com upload + status ("Enviando…"/"Pronto ✓"/erro) guardando o `handle`, e repetidor de botões com `<select>` de tipo e campos condicionais por tipo.
- **Como foi feito / decisões:** Validação de botões espelhada no cliente (`validateButtons`) só para feedback imediato; o payload é montado por `buildButtonPayload` (só as chaves que o tipo usa). "Criar" fica bloqueado enquanto o upload não termina ou o cabeçalho de mídia está sem handle. Controles novos com `wa-*`/`.wa-field`/`border-wa-border` (dark-safe).
- **Problemas / pendências:** Falta o teste visual no modo escuro em navegador (o markup usa só classes semânticas já cobertas).
- **Verificação:** `node --check` no TemplatePicker.js; caminho servidor validado pelos testes de endpoint. Criação real contra a Meta pendente (exige `app_id` configurado no canal).

---

### Fase F6 — Testes
**Objetivo:** cobertura verde no Postgres para os caminhos novos.
**Itens:**
- `[paralelo]` Em [tests/test_endpoints.py](../tests/test_endpoints.py): create com `buttons` + `header_handle` (provider Cloud **mockado** — asserir o `components` Graph montado, incluindo `message_send_ttl_seconds` para UTILITY).
- `[paralelo]` Endpoint de upload: mock do `upload_template_example` → `{ok,handle}`; MIME/tamanho inválidos → 400.
- `[paralelo]` Regressão: create só-texto continua funcionando idêntico; canal sem `templates` → 400.
**Pronto quando:** `venv/bin/python -m pytest tests/test_endpoints.py -q` verde contra `WHATSBOT_TEST_DB_URL`.

#### Status de execução — Fase F6
**Estado:** ✅ Concluída (2026-07-21)
- **O que foi feito:** +24 checagens em `tests/test_endpoints.py`: create com mídia+botões (conv e canal), normalização dos 4 tipos, erros de validação (formato inválido, header sem handle, tipo de botão desconhecido, 3 URLs, URL com variável sem exemplo, texto >25, buttons não-lista), upload-example nos dois escopos (200/400/404) e regressão do caminho só-texto. O canal fake ganhou os kwargs novos + `upload_example`.
- **Como foi feito / decisões:** Provider real exercitado à parte com `httpx` stubado (payload Graph + TTL só em UTILITY + os 2 passos do upload); o resto via provider fake, como o restante da suíte.
- **Problemas / pendências:** Duas falhas pré-existentes na suíte (`agent_transfer_alert`) — reproduzidas idênticas em HEAD limpo, sem relação com este plano. A suíte precisa do plugin `protocolos` presente em `storages/plugins/` (não instalado nesta máquina; copiado de `assets/plugin_examples/` só durante a execução).
- **Verificação:** `venv/bin/python tests/test_endpoints.py` → **1502 passed, 2 failed** (contra 1478 passed / mesmas 2 failed em HEAD limpo), no Postgres `whatsbot_test` (`WHATSBOT_TEST_DB_URL` criada nesta sessão).

---

### Fase F7 — Polish (opcional)
**Objetivo:** melhorar a UX de criação.
**Itens:** contadores de caractere (60/1024/60/25) com destaque ao estourar; prévia estilo bolha (cabeçalho/corpo/rodapé/botões); realçar variáveis não-sequenciais. Tudo `wa-*`/dark-safe.
**Pronto quando:** contadores e prévia aparecem e o modo escuro fica legível.

#### Status de execução — Fase F7
**Estado:** ⬜ Não iniciada (opcional — não executada; o form já traz `maxlength` no texto do botão e a prévia textual do corpo)
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

## 8. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Entrega do provider (plugin import-only) | Editar `assets/plugin_examples/whatsapp_cloud/channels.py` **não** atualiza a cópia rodando em `storages/plugins/whatsapp_cloud` | Após F0/F1/F2, regenerar `assets/channel_plugins/whatsapp_cloud-plugin.zip` e **re-importar** na instância (ou copiar a pasta). Documentar no PR. Core (F2 rotas/service, F3-F5 frontend) sobe com o app |
| Segredo na URL | O passo 1 do resumável na referência põe `access_token` na query (é logado) | Passar `Authorization: OAuth` por **header** nos dois passos (P1). Confirmar que a Meta aceita header na criação da sessão (a confirmar) — se não aceitar, restringir log da URL |
| Auth `OAuth` vs `Bearer` | Usar `Bearer` no upload dá 401 | Usar `Authorization: OAuth {token}` **só** no upload; manter `Bearer` no create (`_headers()`) |
| Regras de mistura de botões | Meta rejeita combinações inválidas | Validar o básico no cliente + servidor; deixar a Meta como fonte da verdade e **exibir `error_user_msg`** (o provider já usa `_graph_error`) |
| Upload grande na RAM | `file.read()` de 16 MiB por request | Cap duro de 16 MiB validado **antes** da leitura completa; um upload por vez; `to_thread` para não travar o loop |
| Handle órfão | Upload feito mas usuário não cria o template | Handle da Meta é efêmero/barato; sem persistência local. Sem ação necessária |
| Modo escuro | Radio/select/dropzone novos ilegíveis no dark | `wa-*`/`.wa-field` + teste manual com tema escuro (regra do CLAUDE.md) |
| Backward-compat | Alargar assinatura quebrar chamadas atuais | Campos novos **opcionais** com default `None`; caminho só-texto deve ficar byte-idêntico (teste de regressão em F6) |
| Nome de tool/rota | — | Nenhuma tool de LLM envolvida; endpoints novos são sufixo `…/upload-example`, sem colisão |
| `message_send_ttl_seconds` | Injetar em MARKETING quebra aprovação | Injetar **só** quando `category==UTILITY` (espelha a referência) |

---

## 9. Perguntas em aberto

- **P1 — Auth do upload resumável: header vs token na URL.** ✅ DECIDIDO (2026-07-21): usar `Authorization: OAuth {token}` por **header** nos dois passos (regra do repo: sem segredo na URL). *A confirmar* que a Meta aceita header no `POST /{app_id}/uploads` (a referência usava query); se rejeitar, fallback documentado com log da URL suprimido.
- **P2 — Onde guardar `app_id`.** ✅ DECIDIDO: **credencial do canal** (não setting global do plugin) — cada WABA/App pode diferir; mesma UX das outras credenciais.
- **P3 — Escopo de botões.** ✅ DECIDIDO (D1): estendido (QUICK_REPLY + URL fixo/dinâmico + PHONE_NUMBER + COPY_CODE). FLOW/CATALOG/carrossel fora.
- **P4 — Mídia: resumável vs URL.** ✅ DECIDIDO (D2): upload resumável (handle). `header_url` descartado (campo morto na referência).
- **P5 — Preview rico estilo WhatsApp.** ⏸️ ADIADO (F7 opcional). O form já tem prévia textual do corpo.
- **P6 — Categoria AUTHENTICATION.** ⏸️ ADIADO. O core aceita, mas OTP é fluxo especial; sem demanda.
- **P7 — Analytics/Custos de template (Meta).** ⏸️ ADIADO. Épico à parte; não confundir com o custo por LLM que o WhatsBot já mostra.
- **P8 — Enriquecer a listagem** (filtros de status/categoria/tipo como a referência). ⏸️ ADIADO. A listagem atual já tem busca + badges + delete.
- **P9 — Limites exatos de mistura de botões da Meta.** ⏸️ A CONFIRMAR na doc oficial (Cloud API `message_templates`): comumente ≤10 total, ≤2 URL, ≤1 PHONE_NUMBER, ≤1 COPY_CODE, quick-replies agrupados. Recomendação: (a) validar esses limites no cliente para UX; (b) confiar na Meta como validação final e exibir `error_user_msg`. Não hard-codar limites que possam mudar sem também deixar a Meta decidir.

---

## 10. Apêndice — arquivos-chave (que o executor vai tocar)

**Provider (plugin `whatsapp_cloud` — entrega via zip, ver §8):**
- [assets/plugin_examples/whatsapp_cloud/channels.py](../assets/plugin_examples/whatsapp_cloud/channels.py) — `provider_descriptor` L90/`credential_fields` L99 (F0); novo `upload_example()` perto de L508 (F1); `create_template` L511-568 (F2)
- [assets/plugin_examples/whatsapp_cloud/routes.py](../assets/plugin_examples/whatsapp_cloud/routes.py#L204) — `credential_keys` do `/info` (F0, opcional)
- `assets/channel_plugins/whatsapp_cloud-plugin.zip` — regenerar + re-importar (entrega)

**Contrato / router / service (core):**
- [channels/base.py:348-359](../channels/base.py#L348-L359) — assinatura `create_template` + novo `upload_example` (F1/F2)
- [channels/outbound.py:134-152](../channels/outbound.py#L134-L152) — `create_template` passthrough (sem mudança) + novo `upload_template_example` (F1)
- [app/services/template_service.py:110-127](../app/services/template_service.py#L110-L127) — repasse dos campos novos + função de upload (F2/F4)

**Rotas (core):**
- [server/routes/conversations.py:787-835](../server/routes/conversations.py#L787-L835) — create conv-scoped (F2) + endpoint upload conv (F4)
- [server/routes/channels.py:153-190](../server/routes/channels.py#L153-L190) — create channel-scoped (F2) + endpoint upload channel (F4)

**Frontend (core):**
- [web/static/js/components/contacts/TemplatePicker.js:435-584](../web/static/js/components/contacts/TemplatePicker.js#L435-L584) — `CreateTemplateForm` estendido (F5); ref. do send-side L180-205 (não tocar)
- [web/static/js/services/api.js:657/688](../web/static/js/services/api.js#L657) — helpers de create + upload (F3)

**Testes:**
- [tests/test_endpoints.py](../tests/test_endpoints.py) — create com botões/mídia + upload + validação (F6)

**Referência (somente leitura — fonte de verdade das formas Graph):**
- [enviar-template-meta/api/create-template.php](../enviar-template-meta/api/create-template.php), [api/upload-example.php](../enviar-template-meta/api/upload-example.php), [js/modules/template-creator.js](../enviar-template-meta/js/modules/template-creator.js), [index.php](../enviar-template-meta/index.php)

---

## 11. Checklist de verificação (por mudança)

- [ ] `tests/test_endpoints.py` **verde no Postgres** (`WHATSBOT_TEST_DB_URL`) — create texto (regressão), create botões+mídia (mock), upload, erros de validação
- [ ] Criar template real com **cabeçalho de imagem + botões mistos** → aparece **PENDING** na listagem (badge "Pendente")
- [ ] Caminho legado (só texto) **byte-idêntico** (asserção de payload)
- [ ] `message_send_ttl_seconds:43200` presente **só** em UTILITY
- [ ] Upload: MIME fora da whitelist e arquivo > 16 MiB → **400** com mensagem clara; canal sem `app_id` → erro acionável (não 500)
- [ ] **Nenhum segredo na URL** (token do upload vai por header — P1)
- [ ] **Modo escuro** legível na tela nova (radio/select/dropzone/chips com `wa-*`/`.wa-field`)
- [ ] Provider entregue: zip regenerado + re-importado em `storages/plugins/whatsapp_cloud` (ou cópia) — cópia instalada reflete as mudanças
- [ ] Reload / back-forward do modal sem estado preso; upload em andamento bloqueia "Criar"
