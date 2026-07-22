# Plano 75 — Mensagens que somem no painel: tipo inbound em branco · falha de envio invisível · citação perdida

> **Status:** PLANEJAMENTO · **Data:** 2026-07-22 · **Escopo:** médio (3 workstreams independentes)
> **Origem:** dois casos reais na **mesma conversa** de produção (instância Empresa Exemplo, conversa **14792**): **(1)** msg **633446** (22/07 08:46:51) — o cliente mandou um **card de contato** com o telefone que o atendente tinha acabado de pedir e a bolha apareceu **totalmente vazia**; **(2)** o cliente respondeu **citando** mensagens anteriores e no painel elas chegaram como mensagens soltas — a ponto de o próprio cliente escrever *"nao ta aparecendo para voce no sistema parece"* (msg 633542, 09:26) e o atendente pedir *"caso você tenha respondido selecionando…"* (msg 633538, 09:25). **Método:** leitura do código + `grep`/`sed` com `arquivo:linha`, consulta ao banco de produção (`messages`, `plugins`) e leitura da **documentação oficial da Meta** (apêndice §12).
> Três frentes: **(A)** todo tipo inbound que a Cloud API entrega e o plugin não conhece passa a virar **texto legível** na bolha (sem render rico); **(B)** `statuses[].status == "failed"` da Meta (template/mensagem não entregue) passa a **aparecer no painel** — bolha vermelha + card com o motivo — e a **emitir evento de bus** para automação; **(C)** a **citação/resposta feita pelo cliente** passa a ser capturada (Cloud + Telegram) e a resolver mesmo quando a mensagem citada está fora da página carregada.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 1. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| **D1** ✅ 2026-07-22 | **Só converter em texto.** Não precisa render rico (card de contato clicável, mini-mapa, carrinho). "Só de converter em texto já ajuda bastante." | **Zero mudança de frontend** na frente A (ver §5 F.P.#1). Escopo cai para plugin + rede de segurança no core. |
| **D2** ✅ 2026-07-22 | Escopo da frente A = **WhatsApp Cloud API**. | GOWA/Telegram fora (o bug real é do Cloud). Se o GOWA tiver gap análogo, vira plano próprio — **a confirmar**, não verificado aqui. |
| **D3** ✅ 2026-07-22 | **Sem coluna `media_extras`** nesta rodada. | Nada de migration. O conteúdo estruturado (telefone, e-mail, itens do pedido) vive **dentro do texto** da mensagem. Persistir o dict estruturado fica para um plano futuro. |
| **D4** ✅ 2026-07-22 | Falha de envio precisa **aparecer no sistema**, tanto no envio quanto no recebimento do aviso. | Frente B: `status='failed'` na mensagem (bolha vermelha, já existe) **+** card `role='error'` com o motivo em PT-BR **+** evento de bus. |
| **D5** ✅ 2026-07-22 | **Sem retroatividade.** | A msg 633446 **não é recuperável** — o payload cru só vive em memória (§4). Nada de backfill. Idem para as citações já perdidas. |
| **D6** ✅ 2026-07-22 | **Citação do cliente entra no plano** (era P2 "adiado"; virou pedido explícito após o 2º caso). | Workstream **C**: F9 (capturar `context.id` no Cloud + `reply_to_message` no Telegram) e F10 (resolver a citação fora da janela paginada). Vale para **todos os providers**, não só Cloud. |
| **P** (princípio) | Padrão do repo: **o provider declara, o core só avalia** — nenhum `if provider ==` no core. | A tradução tipo→texto mora **no plugin**; o core ganha só uma rede de segurança genérica (F3). |

---

## 2. Resumo executivo

O `_parse_message` do plugin `whatsapp_cloud` conhece 10 dos ~16 `type` que a Cloud API entrega ([channels.py:838-892](../assets/plugin_examples/whatsapp_cloud/channels.py#L838)). Todo o resto cai num `else` que grava `text=""` e joga o conteúdo real em `media_extras` — dict que **o core nunca persiste** (não existe coluna). Resultado: `messages.content=''` + `media_type='contacts'` ⇒ **bolha muda**, e o dado do cliente evapora.

A frente **A** conserta na origem: uma função pura de formatação, no plugin, que transforma **qualquer** `type` em uma linha de texto PT-BR (`👤 Contato: Barbara J. Johnson — +55 62 9…`). Como `MediaContent.js` já cai num fallback de texto para `media_type` desconhecido ([MediaContent.js:121](../web/static/js/components/contacts/MediaContent.js#L121)), **o frontend não muda**. Bônus: o texto também alimenta o LLM ([messaging_service.py:1080](../app/services/messaging_service.py#L1080) `llm_text = text or ""`), então a IA passa a "ver" o card de contato.

A frente **C** conserta a citação, que tem **dois** defeitos somados (§3.5): o provider **joga fora** o `context.id` que a Meta manda (`0` de **200.864** mensagens inbound do Cloud têm citação; Telegram idem, `0` de 15.036; só o GOWA extrai) **e** o painel resolve a citação **apenas dentro da página carregada** ([ContactDetail.js:292](../web/static/js/components/contacts/ContactDetail.js#L292)), então mesmo a citação correta vira "Mensagem original indisponível" quando o alvo é de outro dia.

A frente **B** conserta um descarte silencioso no core: o plugin **já parseia** `statuses[].errors` corretamente ([channels.py:812-821](../assets/plugin_examples/whatsapp_cloud/channels.py#L812)), mas o dispatch só age em `delivered`/`read` — e o `emit_with_filter("receipt.changed")` está **dentro** desse `if` ([channel_webhook.py:137-160](../server/routes/channel_webhook.py#L137)). Ou seja, hoje **nenhum plugin consegue reagir a uma falha de entrega**, e o operador não vê nada.

---

## 3. Como funciona hoje (mapa)

### 3.1 Caminho de uma mensagem inbound da Cloud API

| # | Etapa | Arquivo:linha | O que acontece com um `type` desconhecido |
|---|---|---|---|
| 1 | Webhook chega | [channel_webhook.py:312](../server/routes/channel_webhook.py#L312) | payload cru guardado em `_RECENT` (50, **em memória**) |
| 2 | `parse_inbound` | [channels.py:757](../assets/plugin_examples/whatsapp_cloud/channels.py#L757) | itera `value.messages[]` e `value.statuses[]` |
| 3 | `_parse_message` | [channels.py:826](../assets/plugin_examples/whatsapp_cloud/channels.py#L826) | **`else` genérico** [L889-892](../assets/plugin_examples/whatsapp_cloud/channels.py#L889): `media_type = msg_type`, `text` fica `""`, conteúdo → `media_extras["payload"]` |
| 4 | Ingest | [message_ingest_service.py:397](../app/services/message_ingest_service.py#L397) | `if not text and not media_type: return` — **passa** (tem media_type) |
| 5 | Fila do batch | [message_ingest_service.py:535-545](../app/services/message_ingest_service.py#L535) | item com `media_type` → vai para `media_items` ([messaging_service.py:832-835](../app/services/messaging_service.py#L832)) |
| 6 | Save | [messaging_service.py:980-989](../app/services/messaging_service.py#L980) | `_saved_text = text or ("[Áudio recebido]" if audio_path else "")` ⇒ **`''`** |
| 7 | Persistência | [message_repo.py:15-24](../db/repositories/message_repo.py#L15) | **não existe parâmetro `media_extras`** — o dict é descartado |
| 8 | Render | [MediaContent.js:45-121](../web/static/js/components/contacts/MediaContent.js#L45) | nenhum ramo casa → cai no fallback de texto com `''` ⇒ **bolha vazia** |
| 9 | LLM | [messaging_service.py:1080](../app/services/messaging_service.py#L1080) | `llm_text = text or ""` ⇒ a IA também não vê nada |

⚠️ **Gotcha decisivo (etapa 8):** o fallback de `MediaContent` já renderiza texto puro para qualquer `media_type` que ele não conhece. **Preencher `text` no passo 3 resolve as etapas 6, 8 e 9 de uma vez** — é por isso que D1 (só texto) é barato.

### 3.2 Estado de produção (medido)

```sql
select media_type, count(*), count(*) filter (where content='') from messages
where media_type not in ('image','audio','video','document','sticker') and media_type is not null;
```

| `media_type` | linhas | vazias |
|---|---|---|
| `interactive` | 40 | **0** ← todas são `type: "button"` (quick-reply de template), que **tem** `.text` |
| `contacts` | 1 | **1** ← o caso reportado (msg 633446) |
| `unsupported` | 1 | **1** ← msg id 179, 15/07, conversa 17 |

### 3.3 Caminho de um `status` da Meta

| # | Etapa | Arquivo:linha | Hoje |
|---|---|---|---|
| 1 | Parse | [channels.py:806-821](../assets/plugin_examples/whatsapp_cloud/channels.py#L806) | ✅ correto — `kind="receipt"` com `media_extras = {status, conversation, pricing, errors}` |
| 2 | Dispatch | [channel_webhook.py:137](../server/routes/channel_webhook.py#L137) | `if status in ("delivered","read") and mid:` — **`sent` e `failed` caem no vazio** |
| 3 | Emit de bus | [channel_webhook.py:145-148](../server/routes/channel_webhook.py#L145) | `emit_with_filter("receipt.changed")` está **DENTRO** do `if` ⇒ plugin nunca vê falha |
| 4 | Repo | [message_repo.py:360-376](../db/repositories/message_repo.py#L360) | `update_status_by_msg_id` só casa `status IN ('sent','delivered')` e a docstring diz explicitamente *"Does not overwrite 'operator' or 'failed'"* |
| 5 | Frontend | [MessageBubble.js:31,71,112](../web/static/js/components/contacts/MessageBubble.js#L31) | ✅ **já existe**: `isFailed` → bolha rosa + `FailedIcon` |
| 6 | WS | [useConversationWsEvents.js:534-549](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L534) | `message_status` troca `m.status` **para qualquer valor** ⇒ basta o backend mandar `failed` |

### 3.5 Caminho de uma **citação** (frente C) — 2 bugs independentes

**Bug C1 — a citação do cliente nunca é capturada (Cloud + Telegram).** O campo existe no contrato (`InboundEvent.reply_to_msg_id`, [channels/events.py:43](../channels/events.py#L43)) e é threadado até o save ([message_ingest_service.py:66](../app/services/message_ingest_service.py#L66) → [:509](../app/services/message_ingest_service.py#L509)), mas **os providers não o preenchem no inbound**:

| Provider | Extrai a citação inbound? | Onde | Medido em produção (`user` com `reply_to_msg_id`) |
|---|---|---|---|
| **GOWA** | ✅ sim — `_extract_reply_to(data)` com probes em várias formas | [gowa/inbound.py:392-430](../gowa/inbound.py#L392), usado em [:607](../gowa/inbound.py#L607) e [:715](../gowa/inbound.py#L715) | ✅ **comprovado end-to-end** (abaixo) |
| **WhatsApp Cloud** | ❌ **não** — o `InboundEvent` de [channels.py:894-910](../assets/plugin_examples/whatsapp_cloud/channels.py#L894) nem tem o argumento | — | **0 / 200.864** ⚠️ |
| **Telegram** | ❌ **não** — o `InboundEvent` de [telegram/channels.py:441-457](../assets/plugin_examples/telegram/channels.py#L441) idem | — | **0 / 15.036** ⚠️ — **reproduzido ao vivo** (abaixo) |

A Meta manda a citação em **`messages[].context.id`** (§12.6 — presente em todo reply, e sempre presente em `button`/`interactive`); o Telegram manda em `message.reply_to_message.message_id`. **O dado chega no webhook e é jogado fora no parse.**

**Reprodução deliberada em produção (2026-07-22 09:53-09:54, canal `telegram_9bf7bdfc`, conversa 138)** — o usuário mandou do Telegram uma mensagem e depois **respondeu citando** a primeira:

| msg (`messages.id`) | hora | `msg_id` | conteúdo | `reply_to_msg_id` |
|---|---|---|---|---|
| 633616 | 09:53:44 | `74056` | `teste` | — |
| **633622** | 09:54:12 | `74057` | `teste respondendo a mensagem` | **`NULL`** ⬅️ deveria ser `74056` |

As duas linhas estão na **mesma conversa e na mesma página** do painel — ou seja, aqui o C2 nem entra: a citação resolveria trivialmente. **Falta só o campo.** Confirma também que o `msg_id` do Telegram é o inteiro puro em string (`"74057"`), então `str(msg["reply_to_message"]["message_id"])` casa exatamente com o `external_msg_id` — **verificado, não inferido** (F9 item 4).

**Contraprova no GOWA (2026-07-22 09:57, canal `gowa_gjOZx4jaNS` "numero_recuperacao", conversa 14970)** — mesmo roteiro, resultado oposto:

| msg | hora | `msg_id` | conteúdo | `reply_to_msg_id` |
|---|---|---|---|---|
| 633630 | 09:57:11 | `3EB035666D2722BFDFA063` | `teste` | — |
| **633631** | 09:57:38 | `3EB0AD7A3F6A10BB1D2C5A` | `teste resposta` | **`3EB035666D2722BFDFA063`** ✅ |

O `_extract_reply_to` do GOWA **funciona na v8.11.0** e o painel desenhou o balão citado corretamente (validado em tela). ⇒ **O GOWA fica FORA da F9** — só ganha teste de regressão (F8 item 3).

⚠️ **Achado colateral (formato do `msg_id`):** o inbound do GOWA grava o **stanza id cru** (`3EB0…`), não o `wamid.HBg…` — 19/19 das mensagens inbound desse canal. O `reply_to_msg_id` vem no **mesmo formato**, então casa. Reforça a regra da F.P.#7/R12: **casamento é por igualdade exata, sem normalizar nada** — três formatos convivem no banco (`wamid.…` do Cloud, `WAID:wamid.…` do Chatwoot, `3EB0…` do GOWA, `74057` do Telegram) e cada um só precisa casar consigo mesmo.

**Bug C2 — a citação de saída "some" quando a mensagem citada é antiga.** É o que o print mostra ("Mensagem original indisponível" na bolha das 08:37). A resolução é **client-side sobre a página carregada**:

```js
// ContactDetail.js:292 — só procura no array já carregado
function findQuoted(msgId) {
  if (!msgId || !messages) return null;
  return messages.find(m => m.msg_id === msgId) || null;
}
```
[ContactDetail.js:291-295](../web/static/js/components/contacts/ContactDetail.js#L291) + [MessageBubble.js:82-97](../web/static/js/components/contacts/MessageBubble.js#L82). Como o painel usa **paginação keyset** (plano 50 — [message_repo.py:88-100](../db/repositories/message_repo.py#L88), rota em [conversations.py:296-354](../server/routes/conversations.py#L296)), uma citação que aponta para uma mensagem de dias antes **nunca** resolve até o operador rolar até lá.

**Verificado no caso real:** a msg **633432** (22/07 08:37) tem `reply_to_msg_id = 'WAID:wamid.…3EB07F77EEE2BBDB389E66'`, e a mensagem alvo **existe** — é a **626303** (21/07 14:46, "CLIENTE EXEMPLO…"), com `msg_id` **idêntico**, mesma conversa. O dado está certo no banco; **só o cliente é que não acha**. (Foi por isso que o mesmo balão aparece resolvido no 1º print e "indisponível" no 2º — janela carregada diferente.)

⚠️ **Gotcha (etapa 4):** template enviado pelo painel é salvo com `status="operator"` ([template_service.py:91](../app/services/template_service.py#L91)) e mensagem do operador idem ([contacts.py:960](../server/routes/contacts.py#L960)). Como `update_status_by_msg_id` só aceita `sent|delivered`, **reusá-la para `failed` seria um no-op silencioso** — precisa de função nova.

### 3.4 Distribuição do plugin (⚠️ crítico para o deploy)

| Onde | Versão | Observação |
|---|---|---|
| Fonte no repo — [assets/plugin_examples/whatsapp_cloud/](../assets/plugin_examples/whatsapp_cloud/) | **1.2.0** | 936 linhas em `channels.py` |
| Zip importável — `assets/channel_plugins/whatsapp_cloud-plugin.zip` | (regerar) | é o artefato de instalação |
| Instalado neste checkout — `storages/plugins/whatsapp_cloud/` | **1.0.0** | 833 linhas — **divergente** |
| **Instalado em PRODUÇÃO** (`select version from plugins`) | **1.1.0** | **divergente do repo** |

⚠️ `whatsapp_cloud` **NÃO** tem upgrade automático: `BUNDLED_AUTO_INSTALL = ("gowa",)` ([bootstrap.py:37](../plugins/bootstrap.py#L37)) e o upgrade version-aware é só do `gowa`. Publicar = **gerar zip + importar manualmente** na tela Plugins. E vale a lição registrada em memória (plugin `protocolos`): **versão maior ≠ superset** — daí a Fase 0.

---

## 4. Por que o caso 633446 não é recuperável

O único lugar com o payload cru é `_RECENT` ([channel_webhook.py:38-39](../server/routes/channel_webhook.py#L38)) — lista **em memória**, cap **50**, exposta em `GET /api/channel-webhook-payloads` (gated por `settings.manage`). Some no restart e rotaciona em minutos numa instância movimentada. O `media_extras` nunca tocou o disco. ⇒ **D5**: sem backfill; o plano só protege daqui pra frente.

---

## 5. Inventário — tipos a converter

Formato confirmado na doc oficial da Meta (§12). Coluna "Hoje" = comportamento verificado em [channels.py:838-892](../assets/plugin_examples/whatsapp_cloud/channels.py#L838).

| `type` | Fonte no payload | Hoje | Texto proposto (PT-BR) | Risco | Esf. |
|---|---|---|---|---|---|
| **`contacts`** | `contacts[].name.formatted_name`, `phones[].phone`/`wa_id`, `emails[]`, `org.company`, `addresses[]`, `urls[]`, `birthday` | ❌ vazio | `👤 Contato: <nome> — <fone1>, <fone2>` (+ linhas extras p/ e-mail/empresa; 1 bloco por contato do array) | baixo | **M** |
| `order` | `order.catalog_id`, `order.text`, `product_items[{product_retailer_id, quantity, item_price, currency}]` | ❌ vazio | `🛒 Pedido do catálogo: N item(ns) — total R$ X` + 1 linha por item + `order.text` | baixo | S |
| `system` | `system.body` (frase pronta da Meta), `system.wa_id`, `system.type` | ❌ vazio | usar `system.body` direto, prefixado: `ℹ️ <body>` | baixo | S |
| `unsupported` | `messages[].errors[{code,title,error_data.details}]`, `unsupported.type` | ❌ vazio | `⚠️ Mensagem não suportada pelo WhatsApp Business (<unsupported.type>): <title>` | baixo | S |
| `interactive` / `button_reply` | `interactive.button_reply.{id,title}` | ⚠️ **latente**: lê `inter.get("text")`, chave inexistente ⇒ vazio | `<title>` (texto puro — é literalmente o que o cliente clicou) | **médio** | S |
| `interactive` / `list_reply` | `interactive.list_reply.{id,title,description}` | ⚠️ latente | `<title>` (+ ` — <description>` se houver) | médio | S |
| `interactive` / `nfm_reply` (Flows) | `interactive.nfm_reply.response_json` (**string** JSON) | ⚠️ latente | `📋 Formulário respondido: k=v · k=v` (parse defensivo; fallback = json cru truncado) | médio | M |
| `button` (quick-reply de template) | `button.{text,payload}` | ✅ funciona (40 linhas em prod) | manter — só migrar para o formatter sem mudar a saída | baixo | S |
| `request_welcome` | — | ❌ vazio | **NÃO CONFIRMADO** na doc atual (§12.5) — tratar pelo fallback genérico, sem ramo próprio | baixo | — |
| **fallback genérico** | qualquer `type` novo da Meta | ❌ vazio | `⚠️ Mensagem do tipo "<type>" não suportada` | baixo | S |

**Extras da mesma passada:** `context.id` → `reply_to_msg_id` **virou a F9** (frente C, §3.5 — a citação do cliente se perde hoje). `referral` → prefixo `📣 Veio do anúncio "<headline>"` continua **adiado** (P2): merece UI própria de atribuição de campanha.

### Falsos positivos descartados

| # | Hipótese | Por que NÃO é problema |
|---|---|---|
| **1** | "Precisa ensinar o frontend a renderizar `contacts`." | ❌ [MediaContent.js:121](../web/static/js/components/contacts/MediaContent.js#L121) já cai num `return html\`<span …>${fmt(displayContent)}</span>\`` para qualquer `media_type` fora da lista. Com `content` preenchido, a bolha renderiza. **Frontend não muda na frente A.** |
| **2** | "Precisa de migration (`media_extras`)." | ❌ D3 adia. O texto vai em `messages.content`, coluna que já existe. **Zero DDL no plano inteiro.** |
| **3** | "`interactive` está quebrado em produção." | ⚠️ Meio: as 40 linhas em prod são `type: "button"` (tem `.text`). `button_reply`/`list_reply` **nunca chegaram** — é bug **latente**, não regressão observada. Corrigir junto porque é a mesma linha de código. |
| **4** | "Dá pra reusar `update_status_by_msg_id` para `failed`." | ❌ [message_repo.py:374](../db/repositories/message_repo.py#L374) filtra `status IN ('sent','delivered')`; template/operador salvam `'operator'` ⇒ no-op silencioso. Precisa de `mark_failed_by_msg_id`. |
| **5** | "Falha de template já aparece — o envio retorna 502." | ⚠️ Coisas diferentes: o 502 ([channels.py:188](../server/routes/channels.py#L188)) cobre falha **síncrona** da chamada Graph. O caso do usuário é a falha **assíncrona** (Meta aceita o POST e depois manda `status:"failed"` no webhook) — hoje 100% invisível. |
| **6** | "Códigos 470 e 63016 (janela de 24h)." | ❌ Não existem na Cloud API: **470** é On-Premises legado e **63016** é da Twilio. O equivalente é **131047** (§12.9). Não implementar. |
| **7** | "O prefixo `WAID:` em `reply_to_msg_id` está corrompendo a citação." | ❌ **Não é bug.** `WAID:wamid.…` é o `source_id` das mensagens **importadas do Chatwoot** (301.368 linhas em prod têm `msg_id LIKE 'WAID:%'`). A msg 633432 aponta para a 626303 e os dois lados usam o prefixo — **casa exatamente**. A falha é a janela paginada (C2), não o id. **Não normalizar/strippar o prefixo** — isso quebraria as citações que hoje funcionam. |
| **8** | "A citação inbound é problema só do WhatsApp Cloud." | ⚠️ **Não** — o **Telegram** também não extrai (0/15.036). O **GOWA** extrai ([gowa/inbound.py:392](../gowa/inbound.py#L392)). Logo C1 tem 2 call sites, e o teste de regressão do GOWA protege contra quebrar o que já funciona. |

---

## 6. Waves e paralelização

```
WAVE 0   F0 (paridade prod × repo do plugin)                          🔴 barreira dura
             │  (F0 decide a BASE de código de F1/F2/F9)
             ▼
WAVE 1   F1 (formatter puro) · F3 (rede de segurança core) · F4 (repo: mark_failed) · F10 (citação server-side)
             └── as quatro são 🟢 e INDEPENDENTES entre si (arquivos disjuntos)
             │
WAVE 2   F2 [dep F1] · F9 [dep F0]  ─┐        F5 [dep F4] → F6 [dep F5]
             └── F2 e F9 tocam o MESMO arquivo (channels.py) ⇒ mesma pessoa/commit, mas
                 independentes de F5/F6 (core) ⇒ 🟢 entre workstreams
             ▼
WAVE 3   F7 (empacotar + publicar)  🔴   ·   F8 (testes de integração) 🟢
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | Infra/deploy | 🔴 sozinha | alto | Sei qual código roda em prod e qual base editar |
| 1 | **F1** | A — plugin | 🟢 | baixo | `node`-free: pytest do formatter verde |
| 1 | **F3** | A — core | 🟢 | baixo | Tipo novo nunca mais grava `content=''` |
| 1 | **F4** | B — DB | 🟢 | baixo | `mark_failed_by_msg_id` testada |
| 1 | **F10** | C — core+front | 🟢 | baixo | Citação antiga resolve sem rolar a conversa |
| 2 | **F2** | A — plugin | 🟢 `[dep: F1]` | médio | Payloads da doc viram texto no `parse_inbound` |
| 2 | **F9** | C — plugins | 🟢 `[dep: F0]` | baixo | Reply do cliente vira balão citado (Cloud + Telegram) |
| 2 | **F5** | B — core | 🟢 `[dep: F4]` `[bloqueia: F6]` | médio | `failed` marca a msg + emite bus sempre |
| 2 | **F6** | B — core | 🔴 `[dep: F5]` | baixo | Card vermelho com motivo em PT-BR no chat |
| 3 | **F7** | Deploy | 🔴 sozinha | alto | Zip 1.3.0 importado em prod, canal vivo |
| 3 | **F8** | Testes | 🟢 | baixo | Suíte verde no Postgres de teste |

**Despache junto:** `F1 · F3 · F4 · F10` (wave 1) e depois `F2+F9 · F5` (wave 2 — F2 e F9 no mesmo arquivo, então um executor só para as duas). F0, F6 e F7 são sequenciais.

---

## 7. Fases

### F0 — Paridade prod × repo do plugin `whatsapp_cloud` 🔴

**Objetivo:** garantir que a edição parte do código que **realmente roda em produção** (prod=1.1.0, repo=1.2.0, checkout local=1.0.0 — §3.4).

**Itens** `[sequencial]`:
1. Baixar o instalado em prod: `GET /api/plugins/whatsapp_cloud/export` (autenticado) → `whatsapp_cloud-prod-1.1.0.zip`.
2. `diff -ru` do zip contra [assets/plugin_examples/whatsapp_cloud/](../assets/plugin_examples/whatsapp_cloud/) (1.2.0). **Não assumir que 1.2.0 ⊃ 1.1.0** — a lição do plugin `protocolos` foi exatamente essa.
3. Registrar no "Status de execução" o que existe **só** em 1.1.0 (se houver) e portar para o repo **antes** de F1/F2.
4. Confirmar que o `else` genérico de [channels.py:889](../assets/plugin_examples/whatsapp_cloud/channels.py#L889) é idêntico nas duas cópias (é a linha que o plano ataca).
5. Sincronizar `storages/plugins/whatsapp_cloud/` local (1.0.0) com a fonte para o dev-server bater com o que se está editando.

**Pronto quando:** o diff está registrado, o repo contém tudo que prod tem, e `grep -n "else:" assets/.../channels.py` mostra o mesmo bloco visto em prod.

#### Status de execução — Fase 0
**Estado:** ⛔ **CONCLUSÃO INVALIDADA** — ver o bloco "CORREÇÃO" no fim desta fase. A F0 deu OK para editar sobre `assets/`, e esse OK estava **errado**.

##### ⛔ CORREÇÃO (2026-07-22, descoberta na revisão adversarial pós-implementação)

A F0 concluiu "1.2.0 ⊃ 1.1.0, pode editar sobre `assets/`" com base na ancestralidade do git **dentro deste checkout**. O raciocínio estava certo; **a premissa não**: nunca verifiquei se a branch estava atrás do remoto. Estava — **16 commits atrás de `origin/developer`, 0 à frente**.

Nesses 16 commits está o **plano 73** (`feat(templates): cabeçalho de mídia + botões na criação e remetente no modal`, f942cb7) e o **plano 64** no Telegram. Estado real:

| Onde | whatsapp_cloud | telegram | Contém |
|---|---|---|---|
| **Produção** | 1.1.0 | 1.0.0 | nem 73 nem 75 |
| Este checkout, antes do plano 75 | 1.2.0 | 1.0.0 | — |
| Este checkout, depois do plano 75 (F2/F9) | ~~1.3.0~~ → **1.4.0** | ~~1.1.0~~ → **1.2.0** | **só o 75** |
| **`origin/developer`** | **1.3.0** (1065 linhas) | **1.1.0** (521 linhas) | **só o 73/64** |
| `storages/plugins/` local instalado | = `origin/developer` | = `origin/developer` | só o 73/64 |

**Nenhum lado é superset do outro** — é a lição do `protocolos` ("versão maior ≠ superset") reaparecendo:
- só no upstream: credencial `app_id`, `upload_example` (upload resumável para cabeçalho de mídia de template), `own_phone` derivado de `display_phone_number`; no Telegram, `disable_content_type_detection` (plano 64) e o módulo `mode.py` inteiro;
- só aqui: `inbound_text.py`, os ramos de tipo do F2 e o `reply_to_msg_id` do F9.

Pior: os dois lados chegaram a reivindicar **os mesmos números** (1.3.0/1.1.0) com conteúdos diferentes — uma mina para quem fizesse o merge, porque "pegar o meu" apaga o 73 em silêncio e "pegar o deles" apaga o 75. **Desarmado renumerando este lado para 1.4.0 / 1.2.0** (só `plugin.yaml`; nenhuma operação de git).

**Consequências travadas:**
1. **F7 está PARADO** — gerar o zip a partir deste `assets/` produziria um pacote que **destrói o plano 73** na instalação. Ver o Status de execução da F7.
2. `storages/plugins/` **NÃO foi sincronizado** (o usuário pediu a outra sessão para puxar os plugins do upstream; a cópia instalada é a boa e não pode ser sobrescrita).
3. O merge dos 16 commits ficou com **outra sessão de IA** (decisão do usuário, 2026-07-22). Mapa de colisão para quem fizer:

   | Arquivo do plano 75 | Upstream mexeu | Colide |
   |---|---|---|
   | `assets/plugin_examples/whatsapp_cloud/channels.py` · `telegram/channels.py` | reescrita grande | ⚠️ **sim, de frente** — resolução correta = **base do upstream + delta do 75**, versão 1.4.0/1.2.0 |
   | `app/services/message_ingest_service.py` | linhas 488-499 | ⚠️ **sim** — a F3 editou 483 e 505-521 |
   | `app/services/messaging_service.py` | ~38/213/304/565 | não (F3 está na ~980) |
   | `server/routes/conversations.py` | ~706+ (rota nova de template) | não (F10 está na ~385) |
   | `web/static/js/components/contacts/ContactDetail.js` | várias regiões | provavelmente não (F10 está na ~291) |
   | `channel_webhook.py` · `message_repo.py` · `transcription.py` · `MessageBubble.js` · `message_errors.py` · `plugins/events.py` | **não tocou** | ✅ seguros |

**Lição para o próximo plano que mexa em plugin:** a checagem de paridade tem que incluir `git fetch && git rev-list --left-right --count origin/<branch>...HEAD` **antes** de qualquer conclusão sobre linhagem. Comparar só o que está no disco local responde a pergunta errada.

---

**Registro original da F0 (mantido para auditoria — a conclusão dele é a que foi invalidada acima):**
- **O que foi feito:** nenhuma edição de código. Levantamento de paridade:
  | Cópia | Versão | Fonte |
  |---|---|---|
  | `assets/plugin_examples/whatsapp_cloud/` | **1.2.0** | 936 linhas |
  | `assets/channel_plugins/whatsapp_cloud-plugin.zip` | **1.0.0** | artefato **obsoleto** (Jul 6) — `channels.py` byte-idêntico ao `storages/` local |
  | `storages/plugins/whatsapp_cloud/` (checkout local) | **1.0.0** | 833 linhas |
  | **PRODUÇÃO** | **1.1.0** | ⚠️ **não exportável** — `GET /api/plugins` em prod devolve **401** (sem credencial nesta sessão) |
- **Como foi feito / decisões:** o export de prod ficou impossível, então a paridade foi provada **pela ancestralidade do git**, que é evidência mais forte que um diff de zip: as três versões do plugin são uma linha reta no repo — `1.0.0` (8e85008) → `1.1.0` (**dbdc592**) → `1.2.0` (**7321df4**) — e `git diff dbdc592 7321df4 -- assets/plugin_examples/whatsapp_cloud/` é **puramente aditivo**: +29 linhas, um único bloco `try: from channels.base import AudioLimits … except ImportError: pass` (transcode de áudio, plano 65). **Zero remoção, zero alteração.** ⇒ **1.2.0 ⊃ 1.1.0 comprovado**, e editar sobre `assets/` (1.2.0) não pode apagar nada de prod — desde que o 1.1.0 de prod seja o dbdc592 e não um fork editado à mão.
  Diferença crucial em relação à lição do `protocolos` (memória `plugin-changes-distributed-via-zip`): o `protocolos` é mantido **fora** deste repo e chegava por zip de terceiro; o `whatsapp_cloud` é **rastreado no git aqui** e nunca teve commit de fork.
- **Problemas / pendências:**
  1. ⚠️ **Pendência transferida para a F7 (bloqueante da publicação, não do desenvolvimento):** antes de importar a 1.3.0 em prod, **exportar o 1.1.0 instalado** (`GET /api/plugins/whatsapp_cloud/export`, autenticado no painel) e rodar `diff -ru` contra `git show dbdc592:assets/plugin_examples/whatsapp_cloud/channels.py`. Se bater ⇒ publicar direto. Se divergir ⇒ portar o delta **antes** de gerar o zip. O zip também é o rollback.
  2. O zip em `assets/channel_plugins/` está **duas versões atrás** (1.0.0). Será regerado na F7 — não é regressão nova, é dívida pré-existente.
  3. `storages/plugins/whatsapp_cloud/` local (1.0.0) **NÃO** foi sincronizado de propósito: serve de cobaia viva da **F3** (a rede de segurança do core tem que funcionar com plugin antigo instalado — é exatamente o "Pronto quando" da F3).
- **Verificação:** `git log --format=%H -- …/plugin.yaml` + `git show <c>:…/plugin.yaml | grep ^version` (ancestralidade linear); `git diff dbdc592 7321df4 --stat` (+30/-1, só o bloco AudioLimits); `unzip` do zip + `diff -q` contra `storages/` (idênticos, 1.0.0); `curl -s -o /dev/null -w %{http_code}` em prod → `401`.

---

### F1 — Formatter puro `inbound_text.py` no plugin 🟢 `[wave 1]`

**Objetivo:** uma função **pura** (sem rede, sem DB, sem import do core) que recebe o dict `messages[]` da Meta e devolve `str`.

**Itens** `[paralelo entre si]`:
1. Criar `assets/plugin_examples/whatsapp_cloud/inbound_text.py` com:
   ```python
   def describe_message(msg: dict) -> str: ...          # despacha por msg["type"]
   def describe_contacts(contacts: list[dict]) -> str: ...
   def describe_order(order: dict) -> str: ...
   def describe_interactive(inter: dict) -> str: ...
   def describe_unsupported(msg: dict) -> str: ...
   ```
   Regras: **tudo opcional** (a doc só garante `name.formatted_name`+`phones` no exemplo mínimo — §12.1); nunca levantar exceção; string vazia ⇒ o chamador usa o fallback genérico.
2. Formato do card de contato (D1 — texto, uma bolha):
   ```
   👤 Contato: Barbara J. Johnson
   📞 +1 (415) 555-0829
   ✉️ cliente.exemplo@example.com
   🏢 Social Tsunami
   ```
   Vários contatos no mesmo array ⇒ blocos separados por linha em branco.
3. Priorizar `phones[].phone` (formatado, como o cliente vê) e, quando divergir, acrescentar o `wa_id` — **é o dado que o atendente perdeu** no caso 633446.
4. Testes puros em `tests/test_plano75_cloud_inbound_text.py` usando **os JSONs literais do apêndice §12** como fixtures (contacts, order, system, unsupported, button, button_reply, list_reply) + casos degenerados: array vazio, contato só com nome, `nfm_reply` com `response_json` inválido, `type` inventado.
5. **Não** importar nada do core (o plugin precisa carregar em instalação antiga — mesmo cuidado do import defensivo de `MediaLimits`).

**Pronto quando:** `venv/bin/python -m pytest tests/test_plano75_cloud_inbound_text.py -q` verde, e cada payload do §12 produz uma string não-vazia e legível.

#### Status de execução — Fase 1
**Estado:** ✅ Concluída
- **O que foi feito:** `assets/plugin_examples/whatsapp_cloud/inbound_text.py` (novo, 429 linhas), stdlib-only (`json`+`typing`), zero import do core. API: `describe_message` (despacha por `msg["type"]`), `describe_contacts`, `describe_order`, `describe_system`, `describe_unsupported`, `describe_interactive`. Card de contato sai como `👤 Contato: <nome>` + linhas de telefone/e-mail/empresa/cargo/endereço/url/aniversário, cada uma só se existir. `tests/test_plano75_cloud_inbound_text.py` (novo) com **119 casos**.
- **Como foi feito / decisões:** pureza em três camadas — helpers totais que nunca assumem tipo, `try/except Exception` em cada função pública devolvendo `""`, e um teste que faz **grep no próprio fonte** procurando `from channels`/`from db`/`from server` para impedir que alguém introduza dependência de core depois. O `wa_id` só aparece quando os DÍGITOS divergem do `phone` (no payload oficial divergem: 415 vs 412), com rótulo explícito porque o telefone já contém parênteses. `describe_interactive` resolve o subtipo por `inter["type"]` e, se desconhecido, INFERE pela chave presente — essa ordem é o que garante que as 40 linhas de `type:"button"` em produção saiam byte-idênticas.
- **Problemas / pendências:** nenhum. `describe_message` devolve `""` de propósito para `text` e mídia (esses ramos continuam com o parse atual) — a F2 usa o retorno só quando não-vazio.
- **Verificação:** `pytest tests/test_plano75_cloud_inbound_text.py -q` → **119 passed em 0,27s** (o tempo confirma que nenhuma fixture de banco foi acionada). Fuzz de 30 mil payloads em `describe_message`: 100% `str`, nenhuma exceção.

---

### F3 — Rede de segurança no core (nunca gravar bolha muda) 🟢 `[wave 1]`

**Objetivo:** mesmo que um provider (qualquer um) devolva `text=""` com `media_type` sem corpo visível, o painel **nunca** mostra bolha vazia. Genérico — sem `if provider ==`.

**Itens** `[sequencial dentro da fase]`:
1. [messaging_service.py:980](../app/services/messaging_service.py#L980) — hoje:
   `_saved_text = text or ("[Áudio recebido]" if audio_path else "")`.
   Passar a: se `_saved_text` vazio **e** `_saved_media_path` vazio **e** `media_type` não está na lista de mídias renderizáveis (`image/audio/video/sticker/document/location/live_location`) ⇒ `f"[Mensagem do tipo \"{media_type}\" não suportada]"`.
   ⚠️ Cuidado: mídia **com** `media_path` e sem legenda é legítima (a bolha mostra a mídia) — não pode ganhar placeholder.
2. Espelhar em [message_ingest_service.py:483](../app/services/message_ingest_service.py#L483) (`broadcast_msg.content` do t=0) para o texto otimista bater com o salvo.
3. Caminho grupo-sem-menção ([message_ingest_service.py:505-521](../app/services/message_ingest_service.py#L505)) usa o mesmo helper.
4. Extrair o helper para um único lugar (ex.: `server/transcription.py`, ao lado de [`format_media_content`](../server/transcription.py#L63)) para os 3 call sites compartilharem.

**Pronto quando:** um payload sintético com `type` inventado gera uma bolha com `[Mensagem do tipo "xyz" não suportada]` **mesmo com o plugin antigo instalado** (é a defesa contra F7 não ser publicado).

#### Status de execução — Fase 3
**Estado:** ✅ Concluída
- **O que foi feito:** `server/transcription.py:104-138` — `RENDERABLE_MEDIA_TYPES` (image, audio, video, sticker, document, location, live_location) + `placeholder_for_unrenderable(text, media_type, media_path)`. Usado nos **três** call sites: `messaging_service.py:984-990` (save do batch), `message_ingest_service.py:484-490` (broadcast otimista t=0) e `:514/:526` (grupo-sem-menção). `tests/test_plano75_safety_net.py` (novo, 19 testes).
- **Como foi feito / decisões:** a lista de tipos renderizáveis foi **lida** de `MediaContent.js:45-121`, não chutada. A condição exige `media_path` vazio **E** tipo fora da lista (risco R5) — localização está protegida por dois motivos independentes: o `media_path` dela é a string `geo:lat,lng` (truthy) e o tipo está na allow-list. O fallback `[Áudio recebido]` ficou intacto e o helper roda depois dele. O teste de integração dirige `deps.ingest_event` com um `InboundEvent` sintético (`provider="test"`) em vez de postar no webhook do Cloud — deliberado: esta fase é a defesa que precisa valer para **qualquer** provider e com plugin desatualizado, então não pode depender do parser de plugin nenhum.
- **Problemas / pendências:** o `llm_text` do turno continua `text or ""` (`messaging_service.py:1080`), ou seja o placeholder entra no **histórico** que a IA relê, mas não na entrada do turno. Aceitável (melhor que o vazio de hoje), mas registrado.
- **Verificação:** `pytest tests/test_plano75_safety_net.py -q` → 19 passed. `tests/characterization/test_webhook_characterization.py` → 26 passed (pipeline webhook→ingest→batch intacta). Regressão explícita por tipo de mídia legítima: nenhum ganhou placeholder.

---

### F4 — `message_repo.mark_failed_by_msg_id` 🟢 `[wave 1]`

**Objetivo:** poder marcar uma mensagem de saída como `failed` **sem** reusar a função monotônica de acks (F.P.#4).

**Itens:**
1. Nova função em [db/repositories/message_repo.py](../db/repositories/message_repo.py) (ao lado de `update_status_by_msg_id`, L360):
   ```python
   def mark_failed_by_msg_id(msg_id: str) -> dict | None:
       """Marca a msg de SAÍDA como 'failed'. Sobrescreve sent/delivered/operator;
       NUNCA sobrescreve 'read' (já foi lida ⇒ não pode ter falhado) nem 'failed'
       (idempotente). Retorna a row (id, contact_id, conversation_id, content) ou None."""
   ```
2. **Sem cascata** (ao contrário de `update_status_by_msg_id`): falha é de **uma** mensagem específica.
3. Devolver `conversation_id`/`contact_id` — F6 precisa deles para gravar o card na thread certa.
4. Testes em `tests/test_plano75_failed_status.py`: operator→failed ✅, sent→failed ✅, read→failed ❌ (no-op), msg_id inexistente → `None`, 2ª chamada idempotente.

**Pronto quando:** os 5 casos passam.

#### Status de execução — Fase 4
**Estado:** ✅ Concluída
- **O que foi feito:** `db/repositories/message_repo.py:415-464` — `_FAILABLE_STATUSES = ("sent","delivered","operator")` e `mark_failed_by_msg_id(msg_id) -> dict | None`, inseridas logo após `update_status_by_msg_id` (diff: **50 inserções, 0 remoções**). `tests/test_plano75_failed_repo.py` (novo, 7 testes).
- **Como foi feito / decisões:** um único `UPDATE ... WHERE msg_id = :id AND status IS NOT NULL AND status IN (...) RETURNING id, contact_id, conversation_id, content, msg_id` — uma ida ao banco, sem read-then-write. É isso que dá **atomicidade** (dois webhooks simultâneos: em READ COMMITTED o segundo reavalia o WHERE e perde) e é o que torna o retorno o **próprio guard de deduplicação**: devolve a row só quando a transição aconteceu. `read` e `failed` ficam de fora da lista, e o `status IS NOT NULL` protege mensagens de ENTRADA (que nascem com status nulo) de serem marcadas por engano. Sem cascata — ao contrário da função vizinha, falha é de uma mensagem específica.
- **Problemas / pendências:** a função não distingue "msg_id inexistente" de "existe mas não transicionou" — decisão do executor, documentada na docstring. **Isso virou relevante depois** (ver correção A): o caso "não existe" é justamente a corrida do envio da IA, e a correção precisou de um helper de existência para separar os dois.
- **Verificação:** `pytest tests/test_plano75_failed_repo.py -v` → 7 passed. Casos: operator→failed, sent→failed, delivered→failed, read→no-op, id inexistente→None, string vazia→None, 2ª chamada→None, e ausência de cascata.

---

### F2 — Ligar o formatter no `_parse_message` 🟢 `[wave 2, dep: F1]`

**Objetivo:** o `else` genérico deixa de existir como buraco.

**Itens** `[sequencial]`:
1. [channels.py:884-888](../assets/plugin_examples/whatsapp_cloud/channels.py#L884) — o ramo `("button","interactive")`: trocar `text = inter.get("text") or ""` por `describe_interactive(inter)`, mantendo `media_type="interactive"` (**não renomear** — `media_type` é dado histórico de 40 linhas em prod).
2. [channels.py:889-892](../assets/plugin_examples/whatsapp_cloud/channels.py#L889) — o `else`: manter `media_type = msg_type` e `media_extras` (útil para plugins via `message.saved`), **acrescentando** `text = describe_message(msg)` com fallback `f'⚠️ Mensagem do tipo "{msg_type}" não suportada'`.
3. Ramos novos explícitos para `contacts` / `order` / `system` antes do `else` (legibilidade; o despacho real está no formatter).
4. Import **defensivo** do módulo novo (`try/except ImportError` → fallback que devolve `""`), pelo mesmo motivo do `MediaLimits`: o zip pode rodar em core mais antigo.
5. `system` **não traz `contacts[]`** (doc §12.3) ⇒ `sender_name` fica vazio; conferir que o ingest não quebra sem nome (usa o contato existente).
6. Bump `version: 1.3.0` em [plugin.yaml](../assets/plugin_examples/whatsapp_cloud/plugin.yaml#L3).

**Pronto quando:** teste de integração (F8) posta os payloads do §12 em `/api/webhook/whatsapp_cloud/{ch}` e a `messages.content` resultante é não-vazia e legível para os 8 tipos.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída (⚠️ mas sobre a base ERRADA — ver a CORREÇÃO da F0)
- **O que foi feito:** `whatsapp_cloud/channels.py` — import defensivo de `describe_message` (`try/except Exception` com stub que devolve `""`); o ramo `button`/`interactive` trocou `inter.get("text")` pelo formatter mantendo `media_type="interactive"` (dado histórico de 40 linhas em produção, não renomeado); o `else` genérico manteve `media_type` e `media_extras` e ganhou `text = describe_message(msg)` com fallback `⚠️ Mensagem do tipo "<type>" não suportada`; ramos explícitos para `contacts`/`order`/`system`. Versão bumpada. `tests/test_plano75_parse_inbound.py` (novo, 42 testes).
- **Como foi feito / decisões:** `except Exception` em vez de só `ImportError` — o import relativo falha de um jeito quando o módulo é carregado fora de pacote e de outro se o zip vier corrompido, e o plugin precisa **carregar** nos dois casos. Verificado nos dois modos: como pacote (`whatsbot_plugins.whatsapp_cloud.channels`) o formatter entra; carregado por `spec_from_file_location` fora de pacote, degrada para o stub e o `contacts` sai como `⚠️ Mensagem do tipo "contacts" não suportada` em vez de quebrar.
- **Problemas / pendências:** ⚠️ **a base está desatualizada** — o `channels.py` deste checkout não tem o plano 73. A resolução correta no merge é **base do upstream + este delta**. Ver a CORREÇÃO da F0 e o Status da F7.
- **Verificação:** `pytest tests/test_plano75_parse_inbound.py` → 42 passed; junto com a F1 → 161 passed. Regressão comparando old×new em 12 payloads de `button`: saída igual. Texto e mídia byte-idênticos ao comportamento anterior.

---

### F5 — `status:"failed"` deixa de ser descartado 🟢 `[wave 2, dep: F4, bloqueia: F6]`

**Objetivo:** a Meta avisa que não entregou ⇒ o sistema registra, mostra e **emite evento**.

**Itens** `[sequencial]` — todos em [channel_webhook.py:134-160](../server/routes/channel_webhook.py#L134):
1. **Mover o `emit_with_filter("receipt.changed", …)` para FORA do `if status in ("delivered","read")`** — passa a valer para `sent`, `failed` e `played`. Acrescentar `errors` ao payload do evento. ⚠️ Ver risco R3 (plugins que já assinam o evento).
2. Ramo novo `elif status == "failed" and mid:` → `mark_failed_by_msg_id` (F4) + `ws_manager.broadcast("message_status", {phone, msg_ids:[mid], status:"failed"})`. O frontend **já** trata ([useConversationWsEvents.js:540-548](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L540)) e pinta a bolha ([MessageBubble.js:71](../web/static/js/components/contacts/MessageBubble.js#L71)).
3. Evento de bus novo **`message.failed`** — o gancho de automação que o usuário pediu:
   `{phone, channel_id, msg_id, error_code, error_title, error_details, conversation_id, ts, raw}`.
4. `status == "sent"`: **não** gravar (a msg já nasce `sent`/`operator`); só emitir o evento. Evita mexer na semântica monotônica existente.
5. Documentar os 3 eventos na tabela de eventos do [CLAUDE.md](../CLAUDE.md) (seção "Events e Filters").

**Pronto quando:** POST de um `statuses[{status:"failed", errors:[{code:131049,…}]}]` (payload literal do §12.8) faz a bolha ficar vermelha no painel aberto **e** um plugin de teste recebe `message.failed`.

#### Status de execução — Fase 5
**Estado:** ✅ Concluída (ampliada pela correção A — ver o fim deste bloco)
- **O que foi feito:** `server/routes/channel_webhook.py:42-77` — `_first_error(errors)`, função pura que extrai `code`/`title`/`details` do primeiro item de `errors[]` da Meta, tolerando `None`, lista vazia, string, dict solto e lista de não-dicts; converte `code` string-numérica para int. `:171-247` — o ramo `receipt` reescrito: o `emit_with_filter("receipt.changed")` **saiu de dentro** do `if delivered/read` e passou a valer para `sent`/`delivered`/`read`/`failed`/`played`, com `status` e `errors` no payload; ramo novo de `failed` que chama `mark_failed_by_msg_id` e faz broadcast `message_status`; evento novo **`message.failed`**. `CLAUDE.md`: tabela de eventos atualizada.
- **Como foi feito / decisões:** o emit sai **depois** da escrita no banco, para que um plugin que consulte o banco no handler veja o estado novo. `status == "sent"` não grava nada (a mensagem já nasce `sent`/`operator`; mexer ali quebraria a semântica monotônica) — só emite. A limpeza de não-lidas do `read` continua no mesmo lugar, agora antes do emit.
- **Problemas / pendências:** R3 verificado na prática — **nenhum** plugin instalado assina `receipt.changed` nem `*`, e o GOWA só emite receipt para `delivered`/`read` (`gowa/inbound.py:587`), então tirar o emit do `if` só acrescenta `sent`/`failed` do Cloud. `message.failed` **faltava** em `plugins.events.KNOWN_EVENTS` (achado da revisão) — corrigido depois, junto com uma nota de que `receipt.changed` mudou de alcance.
- **Verificação:** teste ponta a ponta pelo webhook real com o payload do §12.8 → 4 passed (marca a msg, broadcast, `receipt.changed` com `status`+`errors`, `message.failed` com `error_code=131049`; reentrega não duplica; `sent` não grava mas emite; falha sobre msg já lida é no-op). `tests/endpoints/test_p26_cloud_webhook.py` → 9 passed. Baseline de `tests/endpoints` medido **com e sem** a mudança: 24 falhas idênticas nos dois.

---

### F6 — Card no chat com o motivo da falha (PT-BR) 🔴 `[wave 2, dep: F5]`

**Objetivo:** o operador entende **por que** não chegou, sem abrir log.

**Itens** `[sequencial]`:
1. Gravar `role="error"` via `message_repo.add(..., conversation_id=<da row de F4>)`. **Por que `error`:** já é painel-only — excluído do contexto do LLM ([message_repo.py:184-185](../db/repositories/message_repo.py#L184)), do preview da sidebar (`LIST_PANEL_ONLY_ROLES`, [_mapping.py:103-106](../db/repositories/_mapping.py#L103)) e já tem card renderizado ([SystemMessageCard.js:203](../web/static/js/components/contacts/SystemMessageCard.js#L203)). **Zero mudança de frontend.**
2. Broadcast `new_message` com esse role (mesmo padrão do `system_notice` em [channel_webhook.py:255-262](../server/routes/channel_webhook.py#L255)).
3. Catálogo `_ERROR_HINTS: dict[int, str]` (código Meta → frase PT-BR acionável), **no core** (é vocabulário do protocolo, não do provider — mas ver P3). Seed a partir de §12.9:

   | Código | Card no chat |
   |---|---|
   | 131047 | "Não entregue: passaram mais de 24h desde a última resposta do cliente. Use um template." |
   | 131049 | "Não entregue: o WhatsApp limitou mensagens de marketing para este cliente. Aguarde antes de reenviar." |
   | 131026 | "Não entregue: número não é WhatsApp, não aceitou os termos ou está com app desatualizado." |
   | 132001 | "Template não existe nesse idioma ou não foi aprovado." |
   | 132000 | "Template com número de parâmetros diferente do cadastrado." |
   | 132015 | "Template pausado por baixa qualidade." |
   | 131053 | "Falha ao subir a mídia da mensagem." |
   | 130472 | "Não enviada: experimento de entrega do WhatsApp (grupo de controle)." |
   | 131000 / demais | fallback: `title` + `error_data.details` da própria Meta |
4. **Fallback sempre existe** — a Meta manda `title`/`message`/`error_data.details` legíveis em inglês; código desconhecido nunca vira card vazio.
5. Deduplicar: 1 card por `msg_id` (a Meta pode reentregar o webhook).

**Pronto quando:** template enviado para número fora da janela de 24h ⇒ bolha vermelha **+** card "passaram mais de 24h…" na conversa.

#### Status de execução — Fase 6
**Estado:** ✅ Concluída
- **O que foi feito:** `server/message_errors.py` (novo, 105 linhas) — `ERROR_HINTS: dict[int, str]` com 13 códigos da Meta em PT-BR acionável, `UNKNOWN_REASON`, `_coerce_code` e `describe_failure(code, title, details)`. `server/routes/channel_webhook.py` — grava `role="error"` na conversa da row devolvida pelo `mark_failed_by_msg_id` e faz broadcast `new_message`. `tests/test_plano75_error_card.py` (novo, 14 testes).
- **Como foi feito / decisões:** catálogo no **core** (P3 decidido): é dicionário inerte, vocabulário do protocolo, sem nenhum `if provider ==` de comportamento. `describe_failure` tem cascata de três níveis — código conhecido → frase PT-BR + `(código N)`; código desconhecido → `title`+`details` da própria Meta (com dedup, porque a Meta costuma repetir os dois); nada → o próprio código. **Nunca devolve string vazia.** Os códigos 470 e 63016 ficaram de fora (F.P.#6: não existem na Cloud API). `bool` é rejeitado explicitamente em `_coerce_code` (é subclasse de `int` e nunca é um código real). Zero mudança de frontend: `role='error'` já é painel-only (fora do contexto do LLM em `message_repo.get_context`, fora do preview em `LIST_PANEL_ONLY_ROLES`) e já tem card em `SystemMessageCard.js:203` — os três foram conferidos por leitura.
- **Problemas / pendências:** R4 continua aberto — `MessageBubble.js:71` pinta a bolha de falha com hex inline `#fce8e8` + `text-wa-text`, ilegível no modo escuro. O **card** desta fase usa cores semânticas e está OK; a **bolha** não. Com a F5 essa bolha deixa de ser rara.
- **Verificação:** `pytest tests/test_plano75_error_card.py -q` → 14 passed. O critério do plano foi exercitado literalmente: POST do §12.8 com `code=131047` ⇒ `status='failed'` na mensagem-alvo, **exatamente 1** linha `role='error'` na mesma conversa contendo "24h"/"template"/"(código 131047)", broadcast `new_message` com conteúdo idêntico ao gravado, e o card ausente de `get_context`.

---

### F9 — Capturar a citação do cliente (Cloud + Telegram) 🟢 `[wave 2, dep: F0]`

**Objetivo:** quando o cliente responde citando, o painel mostra o balão citado — como já acontece no GOWA e como o WhatsApp mostra no celular. **Bug C1 (§3.5).**

**Itens** `[paralelo entre os 2 providers]`:
1. **WhatsApp Cloud** — [channels.py:894-910](../assets/plugin_examples/whatsapp_cloud/channels.py#L894): extrair `msg.get("context", {}).get("id")` e passar `reply_to_msg_id=` no `InboundEvent`. Vale para **todos** os tipos (é campo de mensagem, não de tipo) ⇒ resolver **uma vez**, antes do `return`, não por ramo.
2. ⚠️ **Ignorar `context` que não é citação:** em `button` e `interactive` o `context` está **sempre** presente e aponta para a **nossa** mensagem com os botões (§12.6). Isso é *legítimo* como citação (o WhatsApp mostra assim), mas confirmar que não polui o painel — se poluir, filtrar por `context.from != phone_number_id`.
3. Não confundir com `context.referred_product` (produto do catálogo) nem com `context.forwarded` — só `context.id` vira `reply_to_msg_id`.
4. **Telegram** — [telegram/channels.py:441-457](../assets/plugin_examples/telegram/channels.py#L441): `str(msg["reply_to_message"]["message_id"])`. ✅ **Formato verificado em produção** (§3.5): `external_msg_id` é o inteiro em string (`"74057"`), então os dois lados casam por igualdade exata. Bump da versão do plugin `telegram` + zip (mesmo processo da F7). **Fixture pronta:** conversa 138, `74057` deve apontar para `74056`.
   ⚠️ O Telegram também manda `reply_to_message` em **encaminhamentos de tópico/fórum** (`is_topic_message`) — se o canal usar grupos com tópicos, ignorar quando `msg.get("is_topic_message")` e o alvo for a mensagem de serviço do tópico. **A confirmar** (o canal atual é 1:1).
5. ⚠️ **Não normalizar o prefixo `WAID:`** (F.P.#7) — ids nativos e importados convivem e casam por igualdade exata.
6. **Não mexer no GOWA** — ✅ **comprovado em produção** (§3.5, conversa 14970): extrai e o painel renderiza. Só adicionar teste de regressão com a fixture real (`3EB0AD7A…` → `3EB035666D…`).

**Pronto quando:** POST de um payload Cloud com `context.id` apontando para uma msg existente ⇒ a linha nasce com `reply_to_msg_id` preenchido e o painel desenha o balão citado; idem para um `update` do Telegram com `reply_to_message`.

#### Status de execução — Fase 9
**Estado:** ✅ Concluída (⚠️ mesma ressalva de base da F2)
- **O que foi feito:** **Cloud** — `reply_to_msg_id` extraído de `msg["context"]["id"]` **uma vez, antes do return**, valendo para todos os tipos (`context` é campo de mensagem, não de tipo). **Telegram** — `str(msg["reply_to_message"]["message_id"])`, acesso defensivo. Versões bumpadas. Cobertura em `tests/test_plano75_parse_inbound.py`.
- **Como foi feito / decisões:** **nada de normalizar id** (F.P.#7 / R12) — em produção convivem quatro formatos (`wamid.…` nativo, `WAID:wamid.…` de 301 mil linhas importadas do Chatwoot, `3EB0…` do GOWA, inteiro do Telegram) e cada um só precisa casar consigo mesmo, por igualdade exata. `context.referred_product` e `context.forwarded` **não** viram citação. O GOWA não foi tocado — já funciona, comprovado em produção (§3.5, conversa 14970).
- **Problemas / pendências:** ⚠️ **R10 confirmado e deliberadamente NÃO filtrado** (decisão do usuário, 2026-07-22: *"Manter"*): em `button`/`interactive` o `context` está sempre presente e aponta para a NOSSA mensagem, então toda resposta de botão passa a nascer como balão citando o template. É o comportamento do WhatsApp no celular. Registro técnico: **desligar isso depois não é one-liner** — o filtro sugerido (`context.from == phone_number_id`) não funciona como está, porque `context.from` é o número E.164 do negócio e `phone_number_id` é um id da Meta; exigiria comparar com `metadata.display_phone_number`, que hoje não chega ao `_parse_message`.
- **Verificação:** payload com `context.id` ⇒ `reply_to_msg_id` preenchido; sem `context` ⇒ vazio; Cloud e Telegram. Formato do id do Telegram verificado em produção (`"74057"` deve apontar para `"74056"`).

---

### F10 — Citação fora da janela paginada deixa de sumir 🟢 `[wave 1]`

**Objetivo:** matar o "Mensagem original indisponível" do print quando a mensagem citada **existe** no banco mas está fora da página carregada. **Bug C2 (§3.5).** Independente da F9 — conserta as citações que **já** funcionam (as de saída, 79 linhas em prod).

**Itens** `[sequencial]`:
1. **Escolher a abordagem (P6):**
   - **(a) Hidratar no payload — recomendada.** Ao montar a página de mensagens ([conversations.py:296-360](../server/routes/conversations.py#L296)), para cada linha com `reply_to_msg_id` **não resolvido dentro da própria página**, buscar o alvo em 1 query batch (`WHERE msg_id IN (...) AND contact_id = …`) e anexar `quoted: {msg_id, role, content_snippet, media_type, _id}`. Custo: 1 query por página. `findQuoted` passa a preferir `m.quoted` e só cai no array carregado como fallback.
   - **(b) Endpoint sob demanda** `GET /api/messages/by-msg-id/{msg_id}` chamado pelo componente quando `findQuoted` falha. Mais tráfego, N+1 no scroll.
2. Ajustar [ContactDetail.js:292](../web/static/js/components/contacts/ContactDetail.js#L292) (`findQuoted`) e [MessageBubble.js:82-97](../web/static/js/components/contacts/MessageBubble.js#L82) — `canJump` continua `false` quando o alvo não está carregado (não dá para rolar até uma linha ausente do DOM): mostrar o **conteúdo** da citação sem o clique, em vez de "indisponível".
3. Preservar o texto atual **"Mensagem original indisponível"** para o caso legítimo: alvo **apagado** ou nunca recebido.
4. ✅ **Índice já existe** — verificado em produção: `idx_msg_id ON messages USING btree (msg_id)`. A busca batch por `msg_id IN (...)` é indexada; **nenhum DDL novo** (o plano segue com zero migration).
5. Escopo por contato/conversa na query (evitar vazar citação de outra conversa).

**Pronto quando:** abrir a conversa 14792 direto na URL, sem rolar, e o balão das 08:37 mostrar o texto de "CLIENTE EXEMPLO…" em vez de "Mensagem original indisponível".

#### Status de execução — Fase 10
**Estado:** ✅ Concluída (ampliada pela correção B — ver o fim deste bloco)
- **O que foi feito:** `db/repositories/message_repo.py:634-…` — `QUOTED_SNIPPET_MAX = 120` e `get_by_msg_ids(msg_ids, *, conversation_id=None, contact_id=None)`, acrescentadas **no fim do arquivo** (a função da F4, por volta da linha 415, ficou intacta). `server/routes/conversations.py:44-73` — `_hydrate_quoted(msgs, conv_id)`, chamada em `:387-389` **depois** do corte do over-fetch. `ContactDetail.js` (`findQuoted` prefere o `quoted` do servidor) e `MessageBubble.js` (mostra o conteúdo citado mesmo sem poder pular). `tests/test_plano75_quoted_hydration.py` (novo, 8 testes).
- **Como foi feito / decisões:** abordagem (a) do P6 — hidratar no payload, **1 query em lote por página**, com early-return quando não há citação pendente; a alternativa (endpoint sob demanda) geraria N+1 conforme o operador rola. `get_by_msg_ids` **exige** escopo e levanta `ValueError` sem ele — o vazamento entre conversas é tratado como requisito de segurança, não detalhe. Só hidrata quando o alvo **não** está na própria página (payload não cresce à toa, R11); trecho cortado em 120 chars, sem `raw` nem caminho de mídia. Ordem importa: a hidratação roda **depois** do corte da linha extra do keyset, senão uma mensagem que não está de fato na página contaria como presente. `canJump` continua falso quando o alvo não está no DOM, e "Mensagem original indisponível" foi preservado para o caso legítimo (alvo apagado).
- **Problemas / pendências:** limitação deliberada — o escopo é por `conversation_id`. Não é restritivo demais na prática porque `resolve_for_contact_ex` **reabre** a mesma conversa em vez de criar outra (`conversation_repo.py:370`), então o ciclo fechar/reabrir não perde a citação; alargar para `contact_id` exporia trecho de conversa de outra inbox a operador com visibilidade restrita.
- **Verificação:** `pytest tests/test_plano75_quoted_hydration.py -q` → 8 passed. Fixture com **duas páginas** reais e a citação apontando para a página anterior. Inclui teste com id prefixado `WAID:` (casa) e a forma sem prefixo (não casa) — trava a proibição de normalizar. `node --input-type=module --check` OK nos dois JS.

---

### F7 — Empacotar e publicar o plugin 🔴 `[wave 3]`

**Objetivo:** as correções de plugin (F1/F2 = frente A; **F9** = frente C) chegarem em produção — **não chegam sozinhas** (§3.4).

**Itens** `[sequencial]`:
1. Regenerar `assets/channel_plugins/whatsapp_cloud-plugin.zip` a partir de `assets/plugin_examples/whatsapp_cloud/` (1.3.0), sem `__pycache__`. **Idem `telegram-plugin.zip`** se a F9 tiver mexido no Telegram (prod hoje: `telegram 1.0.0`).
2. Em prod: **exportar o 1.1.0 antes** (rollback) → Plugins → Importar `.zip` → confirmar `version=1.3.0` em `select id,version from plugins`.
3. ⚠️ Importar plugin **existente** pode exigir remover antes ([`POST /api/plugins/import`](../server/routes/plugins.py) checa colisão de `id`). **Confirmar o comportamento** (sobrescreve vs 409) antes de tocar em prod; se for DELETE+import, lembrar que o delete derruba `plugin_<id>_*` e settings namespaceadas — o `whatsapp_cloud` **não** tem tabelas próprias, mas **tem credenciais em `channels`** (não são do plugin, sobrevivem).
4. Restart do servidor (o import agenda) e revalidar: canal Cloud `connected`, webhook respondendo, uma mensagem de texto normal chegando.
5. Considerar (P4) estender `BUNDLED_AUTO_INSTALL`/upgrade version-aware para os providers importáveis, acabando com esse passo manual — **fora do escopo deste plano**.

**Pronto quando:** prod com `whatsapp_cloud 1.3.0`, canal vivo, mensagem de texto normal chegando, e um card de contato de teste aparecendo como texto.

#### Status de execução — Fase 7
**Estado:** ⛔ **BLOQUEADA — não executar até o merge dos 16 commits**
- **O que foi feito:** nada, deliberadamente. Só a renumeração defensiva das versões (`whatsapp_cloud` → **1.4.0**, `telegram` → **1.2.0**) para matar a colisão com o upstream.
- **Como foi feito / decisões:** gerar o zip a partir do `assets/` deste checkout produziria um pacote **destrutivo**: instalá-lo apagaria o plano 73 (credencial `app_id`, `upload_example`/upload resumável, `own_phone`) e o plano 64 do Telegram (`disable_content_type_detection`, `mode.py`). Ver a CORREÇÃO da F0. Pelo mesmo motivo **não** sincronizei `storages/plugins/` — a cópia instalada é a do upstream, puxada de propósito por outra sessão, e é a boa.
- **Problemas / pendências:** ordem obrigatória quando for retomar:
  1. mergear `origin/developer` (outra sessão está com isso);
  2. resolver os dois `channels.py` como **base do upstream + delta do plano 75** — nunca "pegar um lado";
  3. conferir que o resultado tem as duas coisas: `upload_example`/`app_id` (73) **e** `describe_message`/`reply_to_msg_id` (75); no Telegram, `disable_content_type_detection` (64) **e** `reply_to_message` (75);
  4. só então regerar `assets/channel_plugins/whatsapp_cloud-plugin.zip` (1.4.0) e `telegram-plugin.zip` (1.2.0) — os dois estão hoje **três versões atrás** (1.0.0);
  5. em produção (hoje em 1.1.0/1.0.0), **exportar antes** para ter rollback, e conferir se o import sobrescreve ou dá 409.
- **Verificação:** `grep '^version:' assets/plugin_examples/{whatsapp_cloud,telegram}/plugin.yaml` → 1.4.0 / 1.2.0, distintos dos 1.3.0 / 1.1.0 do upstream (`wt-upstream`). `diff` entre `storages/plugins/<id>/channels.py` e o `wt-upstream` correspondente → idênticos (a instalação local é o upstream puro, intocada por este plano).

---

### F8 — Testes de integração ponta a ponta 🟢 `[wave 3]`

**Objetivo:** travar o comportamento contra regressão futura.

**Itens** `[paralelo]`:
1. `tests/test_plano75_cloud_inbound_types.py` — para cada payload do §12: POST em `/api/webhook/whatsapp_cloud/{ch}` → asserir `messages.content` não-vazia + `media_type` esperado. Espelhar o setup de [tests/endpoints/test_p26_cloud_webhook.py](../tests/endpoints/test_p26_cloud_webhook.py) (registra canal + credenciais).
2. `tests/test_plano75_failed_status.py` — o webhook `failed` marca a msg, emite `message.failed`, cria 1 card `role='error'`; reentrega não duplica.
3. `tests/test_plano75_inbound_reply.py` (frente C) — payload Cloud com `context.id` e update Telegram com `reply_to_message` ⇒ `messages.reply_to_msg_id` preenchido; **regressão do GOWA** (continua extraindo); citação com alvo inexistente não quebra o save.
4. Teste da F10: página de mensagens devolve `quoted` hidratado para uma citação cujo alvo está **fora** da página (montar 2 páginas no fixture).
5. Regressão: o `type:"text"` normal e o `button` (40 linhas em prod) continuam **byte-idênticos**.
6. Regressão: msg `role='error'` **não** entra no contexto do LLM nem no preview da sidebar (já garantido por `LIST_PANEL_ONLY_ROLES`, mas assertar).
7. Regressão: `reply_to_msg_id` com prefixo `WAID:` continua casando (F.P.#7) — fixture com id importado do Chatwoot.

**Pronto quando:** `venv/bin/python -m pytest tests/test_plano75_*.py tests/endpoints -q` verde no Postgres de teste.

#### Status de execução — Fase 8
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

## 8. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| **R1 — plugin divergente** (prod 1.1.0 × repo 1.2.0 × local 1.0.0, §3.4) | Publicar 1.3.0 baseada no repo **apaga** algo que só existe em 1.1.0 (lição `protocolos`: versão maior ≠ superset) | **F0 é barreira dura**: exportar prod e diffar antes de qualquer edição |
| **R2 — plugin não atualiza sozinho** | F1/F2 ficam no repo e prod segue com bolha vazia | **F3** (rede de segurança no core) protege mesmo com plugin antigo; F7 é passo explícito |
| **R3 — `receipt.changed` passa a disparar para `sent`/`failed`** | Plugin que assina o evento supondo "só delivered/read" pode reagir errado | Grep antes: hoje só `protocolos/events.py:19` assina `message.sent` (evento **diferente**); nenhum plugin instalado assina `receipt.changed`. Ainda assim: documentar no CLAUDE.md e incluir `status` no payload |
| **R4 — bolha `failed` no modo escuro** | [MessageBubble.js:71](../web/static/js/components/contacts/MessageBubble.js#L71) usa `style="background:#fce8e8"` **hex inline** + `text-wa-text`; no dark o texto fica claro sobre rosa claro ⇒ **ilegível**. Pré-existente, mas F5 torna frequente | Verificar visualmente; se ilegível, trocar por classe `wa-*` ou fixar `text-gray-900` na bolha de falha (regra "Tema e modo escuro" do CLAUDE.md) |
| **R5 — placeholder em mídia legítima (F3)** | Marcar `[não suportada]` numa imagem sem legenda | Condicionar a `not media_path` **e** tipo fora da lista renderizável; teste de regressão explícito |
| **R6 — `nfm_reply` NÃO CONFIRMADO na doc** (§12.6) | Formato pode divergir do de terceiros | `json.loads` defensivo, tolerar campos ausentes, fallback para o genérico. Nunca `raise` |
| **R7 — reentrega de webhook** | Meta reentrega ⇒ card de erro duplicado | Dedup por `msg_id` em F6; ingest de mensagem já tem dedup ([message_ingest_service.py:382-386](../app/services/message_ingest_service.py#L382)) |
| **R8 — PII no texto** | Telefone/e-mail do card agora ficam em `messages.content` **e entram no contexto do LLM** | É o objetivo (o atendente e a IA precisam ver o número). Quem quiser mascarar já tem `filter.message.before_save` / `ai_history_exclude_patterns` |
| **R10 — `context` sempre presente em `button`/`interactive`** (§12.6) | Toda resposta de botão vira balão citando a **nossa** mensagem ⇒ ruído visual novo em 40+ linhas/mês | Validar no painel antes de publicar; se incomodar, filtrar por `context.from == metadata.phone_number_id` (F9 item 2). Comportamento **igual ao do WhatsApp no celular** — provavelmente desejado |
| **R11 — F10 muda o payload da rota de mensagens** | Campo `quoted` novo em cada linha ⇒ payload maior; front antigo ignora (aditivo) | Só hidratar quando `reply_to_msg_id` existe **e** não resolveu na página; snippet truncado (≤120 chars), sem `raw`/mídia |
| **R12 — normalizar `WAID:`** | Tentar "limpar" o prefixo quebraria as 77 citações que hoje funcionam e as 301k msgs importadas | **Proibido** (F.P.#7). Casamento é por igualdade exata, sempre |
| **R9 — `system` sem `contacts[]`** | `sender_name` vazio pode sobrescrever nome | Não chamar `set_wa_name` com string vazia — já é o comportamento ([message_ingest_service.py:409](../app/services/message_ingest_service.py#L409) exige `event.sender_name`), só confirmar |

---

## 9. Perguntas em aberto

**P1 — Persistir `media_extras` (coluna JSONB) agora?**
⏸️ **ADIADO** (D3). Sem ela, o dado estruturado só existe embutido no texto — um plugin não consegue ler "o telefone do card" programaticamente (só via `message.saved`, ao vivo). (a) migration + coluna; (b) só texto. **Recomendação: (b) agora, (a) quando surgir a 1ª automação que precise.**

**P2 — Aproveitar a passada para `context` (citação) e `referral` (anúncio)?**
✅ **DECIDIDO (2026-07-22)** — pedido explícito do usuário após o 2º caso de produção (D6). `context.id` → `reply_to_msg_id` virou a **F9** (+ Telegram, + a F10 para a resolução). **`referral` continua adiado** — merece UI própria de atribuição de campanha, é plano separado.

**P6 — F10: hidratar no payload (a) ou endpoint sob demanda (b)?**
⏸️ **ADIADO — decidir na F10.** (a) 1 query batch por página, payload um pouco maior, zero round-trip extra; (b) `GET /api/messages/by-msg-id/{id}`, payload enxuto, mas N+1 conforme o operador rola. **Recomendação: (a)** — o índice `idx_msg_id` já existe e a citação é campo de exibição, não de interação.

**P3 — O catálogo de códigos de erro fica no core ou no plugin?**
⏸️ **ADIADO — decidir na F6.** (a) core: um dicionário `int→str` reusável e simples; (b) plugin: coerente com "o provider declara" (os códigos são **da Meta**), exigindo um gancho novo tipo `describe_status_error()` no contrato `Channel`. **Recomendação: (a) para esta rodada** (o dicionário é inerte, sem `if provider ==` de comportamento), migrando para (b) quando um 2º provider tiver códigos próprios.

**P4 — Upgrade automático dos plugins de canal importáveis?**
⏸️ **ADIADO.** Hoje só `gowa` tem upgrade version-aware ([bootstrap.py:37](../plugins/bootstrap.py#L37)); `whatsapp_cloud`/`telegram`/`website` exigem import manual (F7). Vira plano próprio.

**P5 — Os demais campos de webhook da WABA (§12.10)?**
⏸️ **ADIADO** — é a "parte 2" que o usuário mencionou. `message_template_status_update`, `phone_number_quality_update`, `user_preferences` (opt-out de marketing), `account_alerts` etc. hoje resultam em **200 OK com 0 eventos** (o `parse_inbound` só lê `messages`/`statuses`). Um evento de bus genérico `webhook.unknown_field` destravaria tudo de uma vez. **F5 já é o primeiro passo dessa direção.**

---

## 10. Checklist de verificação

- [ ] F0: diff prod(1.1.0) × repo(1.2.0) registrado; nada exclusivo de prod foi perdido
- [ ] `venv/bin/python -m pytest tests/test_plano75_*.py -q` verde
- [ ] `venv/bin/python -m pytest tests/endpoints -q` verde (**Postgres de teste**, `WHATSBOT_TEST_DB_URL` com `test` no nome do banco)
- [ ] Regressão: mensagem de **texto** e **mídia** normais na Cloud API inalteradas (conteúdo, `media_type`, `media_path`)
- [ ] Regressão: as 40 linhas `media_type='interactive'` (quick-reply de template) continuam com o mesmo texto
- [ ] Card de contato de teste → bolha com nome **e telefone** visíveis, e a IA "vê" o texto (checar `llm_text`)
- [ ] `type` inventado → `[Mensagem do tipo "xyz" não suportada]` (rede de segurança F3) **com o plugin antigo instalado**
- [ ] `status:"failed"` → bolha vermelha + card com motivo em PT-BR + `message.failed` no bus; reentrega não duplica
- [ ] **Citação (C1)**: cliente responde citando no WhatsApp Cloud ⇒ balão citado aparece; idem Telegram; **GOWA continua funcionando**
- [ ] **Citação (C2)**: abrir a conversa 14792 **sem rolar** ⇒ o balão das 08:37 mostra o texto citado, não "Mensagem original indisponível"
- [ ] Citação com `msg_id` prefixado `WAID:` (importado do Chatwoot) continua resolvendo — prefixo **não** foi normalizado
- [ ] Resposta de botão/template (`context` presente) não poluiu o painel com citação indesejada (R10)
- [ ] **Modo escuro**: bolha de falha (`#fce8e8`) e card `role='error'` legíveis (R4)
- [ ] Sem migration nova (`git diff db/alembic/versions/` vazio)
- [ ] `message_repo.mark_failed_by_msg_id` não sobrescreve `read`
- [ ] F7: `select id,version from plugins where id='whatsapp_cloud'` → `1.3.0` em prod; canal `connected`; zip 1.1.0 guardado para rollback
- [ ] CLAUDE.md atualizado: `message.failed` na tabela de eventos + nota de que `receipt.changed` agora cobre `sent`/`failed`

---

## 11. Apêndice — arquivos-chave

**Plugin (frente A)**
- [assets/plugin_examples/whatsapp_cloud/channels.py](../assets/plugin_examples/whatsapp_cloud/channels.py) — `_parse_message` L826-910 (o `else` em L889)
- `assets/plugin_examples/whatsapp_cloud/inbound_text.py` — **novo** (F1)
- [assets/plugin_examples/whatsapp_cloud/plugin.yaml](../assets/plugin_examples/whatsapp_cloud/plugin.yaml) — bump 1.2.0 → 1.3.0
- `assets/channel_plugins/whatsapp_cloud-plugin.zip` — regerar (F7)

**Core (frentes A e B)**
- [server/routes/channel_webhook.py](../server/routes/channel_webhook.py) — dispatch de `receipt` L134-160 (F5/F6)
- [app/services/messaging_service.py](../app/services/messaging_service.py) — save de mídia L967-1005 (F3)
- [app/services/message_ingest_service.py](../app/services/message_ingest_service.py) — broadcast t=0 L483-497, grupo L525-541 (F3)
- [db/repositories/message_repo.py](../db/repositories/message_repo.py) — `mark_failed_by_msg_id` novo, junto de L360 (F4)
- [server/transcription.py](../server/transcription.py) — helper de placeholder ao lado de `format_media_content` L63 (F3)

**Frente C — citação**
- [assets/plugin_examples/whatsapp_cloud/channels.py](../assets/plugin_examples/whatsapp_cloud/channels.py) L894-910 — `context.id` → `reply_to_msg_id` (F9)
- [assets/plugin_examples/telegram/channels.py](../assets/plugin_examples/telegram/channels.py) L441-457 — `reply_to_message.message_id` (F9)
- [gowa/inbound.py](../gowa/inbound.py) L392-430 — **referência** do extractor que já funciona (não editar)
- [server/routes/conversations.py](../server/routes/conversations.py) L296-360 — hidratação do `quoted` na página (F10)
- [web/static/js/components/contacts/ContactDetail.js](../web/static/js/components/contacts/ContactDetail.js) L292 — `findQuoted` (F10)

**Frontend — verificação (A e B) / edição (C)**
- [web/static/js/components/contacts/MediaContent.js](../web/static/js/components/contacts/MediaContent.js) L121 (fallback de texto — a razão de A não tocar o front)
- [web/static/js/components/contacts/MessageBubble.js](../web/static/js/components/contacts/MessageBubble.js) L31/71/112 (bolha `failed` — checar dark mode, R4) · L82-97 (render da citação — F10)
- [web/static/js/components/contacts/SystemMessageCard.js](../web/static/js/components/contacts/SystemMessageCard.js) L203 (card `role='error'`)

**Testes**
- `tests/test_plano75_cloud_inbound_text.py` · `tests/test_plano75_cloud_inbound_types.py` · `tests/test_plano75_failed_status.py` · `tests/test_plano75_inbound_reply.py` — **novos**
- [tests/endpoints/test_p26_cloud_webhook.py](../tests/endpoints/test_p26_cloud_webhook.py) — modelo de setup de canal Cloud

---

## 12. Apêndice — payloads oficiais da Meta (fixtures dos testes)

> Coletado da doc oficial em 2026-07-22. A doc migrou de `/docs/whatsapp/cloud-api/webhooks/…` para **`/documentation/business-messaging/whatsapp/webhooks/reference/messages/<tipo>`**. Envelope comum a todos: `{object, entry[{id, changes[{value{messaging_product, metadata{display_phone_number, phone_number_id}, contacts[], messages[]|statuses[]}, field:"messages"}]}]}`.

### 12.1 `contacts` — [doc](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/contacts)
```json
{ "from": "16505551234",
  "id": "wamid.HBgLMTY1MDM4Nzk0MzkVAgASGBQzQTRBNjU5OUFFRTAzODEwMTQ0RgA=",
  "timestamp": "1744344496", "type": "contacts",
  "contacts": [ { "name": { "first_name": "Barbara", "last_name": "Johnson",
                            "formatted_name": "Barbara J. Johnson" },
                  "org": { "company": "Social Tsunami" },
                  "phones": [ { "phone": "+1 (415) 555-0829", "wa_id": "14125550829", "type": "MOBILE" } ] } ] }
```
Campos opcionais documentados na tabela (sem aparecer no exemplo): `name.middle_name/prefix/suffix`, `org.department/title`, `emails[{email,type}]`, `addresses[{street,city,state,zip,country,country_code,type}]`, `urls[{url,type}]`, `birthday`. **Trate tudo como opcional.**

### 12.2 `order` — [doc](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/order)
```json
{ "type": "order",
  "order": { "catalog_id": "194836987003835", "text": "Love these!",
    "product_items": [ { "product_retailer_id": "di9ozbzfi4", "quantity": 2, "item_price": 30, "currency": "USD" },
                       { "product_retailer_id": "nqryix03ez", "quantity": 1, "item_price": 25, "currency": "USD" } ] } }
```
⚠️ A doc declara `item_price` como *Integer* mas exemplifica `7.99` — **trate como número decimal**. `order.text` é opcional.

### 12.3 `system` — [doc](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/system)
```json
{ "from": "16505551234", "id": "wamid.HBgLMTk4MzU1NTE5NzQVAgASGAoxMTgyMDg2MjY3AA==",
  "timestamp": "1750269342", "type": "system",
  "system": { "body": "User Sheena Nelson changed from 16505551234 to 12195555358",
              "wa_id": "12195555358", "type": "user_changed_number" } }
```
⚠️ Na doc **atual** é `type: "user_changed_number"` e o número novo vem em **`wa_id`** (não `new_wa_id` — isso era v11/On-Premises). ⚠️ **Não há array `contacts[]`** neste webhook. `system.body` já vem pronto ⇒ usar direto.

### 12.4 `unsupported` + `messages[].errors[]` — [doc](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/unsupported)
```json
{ "type": "unsupported", "unsupported": { "type": "edit" },
  "errors": [ { "code": 131051, "title": "Message type unknown", "message": "Message type unknown",
                "error_data": { "details": "Message type is currently not supported." } } ] }
```
`unsupported.type` ∈ `button, edit, errors, gif, group_invite, hsm, image, interactive, keep_in_chat, link_preview, list, location, media_placeholder, order, pin, poll_creation, poll_update, product, reaction`. Códigos: **131051** (tipo não suportado) e **131060** (mensagem indisponível — 1º contato com número onboarded no app Business).
⚠️ Consequência: **`order`, `location`, `reaction`, `interactive` podem chegar como `unsupported`** dependendo do onboarding do número — não é caminho de exceção, é caminho normal.

### 12.5 `request_welcome` — **NÃO CONFIRMADO**
Sem página na doc atual; indício de remoção junto com `enable_welcome_message` da Conversational Automation API (changelog inacessível, HTTP 500). **Não criar ramo próprio** — cobrir pelo fallback genérico. O substituto atual é `referral.welcome_message.text` (§12.7).

### 12.6 `interactive` — [doc](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/interactive)
```json
{ "context": { "from": "15550783881", "id": "wamid.…" }, "type": "interactive",
  "interactive": { "type": "list_reply",
    "list_reply": { "id": "priority_express", "title": "Priority Mail Express", "description": "Next Day to 2 Days" } } }
```
```json
{ "context": { "from": "15550783881", "id": "wamid.…" }, "type": "interactive",
  "interactive": { "type": "button_reply", "button_reply": { "id": "cancel-button", "title": "Cancel" } } }
```
⚠️ **Não existe a chave `text`** — é `interactive.<subtipo>.title`. É exatamente o bug latente de [channels.py:888](../assets/plugin_examples/whatsapp_cloud/channels.py#L888). `context` está **sempre** presente (o `context.from` é o número do negócio).
⚠️ **`nfm_reply` (Flows) NÃO CONFIRMADO** na doc oficial de webhooks — formato `{response_json (string JSON), body, name}` só em fontes de terceiros ⇒ parse defensivo (R6).

### 12.7 `button` (quick-reply de template) e `referral` — [doc](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/button) · [doc text](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/text)
```json
{ "context": {...}, "type": "button", "button": { "payload": "Unsubscribe", "text": "Unsubscribe" } }
```
```json
{ "referral": { "source_url": "https://fb.me/3cr4Wqqkv", "source_id": "120226305854810726",
                "source_type": "ad", "body": "Summer Succulents are here!", "headline": "Chat with us",
                "media_type": "image", "image_url": "https://…", "ctwa_clid": "Aff-n8ZT…",
                "welcome_message": { "text": "Hi there! Let us know how we can help!" } },
  "type": "text", "text": { "body": "Can I get more info about this?" } }
```
`context.referred_product{catalog_id, product_retailer_id}` aparece quando o usuário tocou "Message business" num produto. `forwarded`/`frequently_forwarded`: descritos em prosa, **sem exemplo JSON** — ler de `context.*` com fallback no nível da mensagem.

### 12.8 `statuses[].status = "failed"` — [doc](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/status)
```json
{ "statuses": [ { "id": "wamid.HBgLMTY1MDM4Nzk0MzkVAgARGBI0QUQ2MjA4NEYyRkExNjMyREUA",
    "status": "failed", "timestamp": "1751142888", "recipient_id": "16505551234",
    "errors": [ { "code": 131049,
      "title": "This message was not delivered to maintain healthy ecosystem engagement.",
      "message": "This message was not delivered to maintain healthy ecosystem engagement.",
      "error_data": { "details": "In order to maintain a healthy ecosystem engagement, the message failed to be delivered." },
      "href": "/documentation/business-messaging/whatsapp/support/error-codes" } ] } ] }
```
`failed` **não traz** `conversation` nem `pricing`. Valores de `status`: `sent`, `delivered`, `read`, `failed`, **`played`**. A partir da v24.0 o `read` vem **sem** `conversation`/`pricing` ⇒ tratar tudo como opcional.

### 12.9 Códigos de erro (verbatim) — [doc](https://developers.facebook.com/documentation/business-messaging/whatsapp/support/error-codes)

| Código | Texto oficial |
|---|---|
| 131047 | *More than 24 hours have passed since the recipient last replied to the sender number.* |
| 131049 | *This message was not delivered to maintain healthy ecosystem engagement.* |
| 131026 | *Unable to deliver message. Reasons can include: The recipient phone number is not a WhatsApp phone number…* |
| 131000 | *Message failed to send due to an unknown error.* |
| 131051 | *Unsupported message type.* |
| 131053 | *Unable to upload the media used in the message.* |
| 130472 | *Message was not sent as part of an experiment.* |
| 132000 | *The number of variable parameter values included in the request did not match the number of variable parameters defined in the template.* |
| 132001 | *The template does not exist in the specified language or the template has not been approved.* |
| 132005 | *Translated text is too long.* · 132007 *Template content violates a WhatsApp policy.* · 132012 *Variable parameter values formatted incorrectly.* |
| 132015 | *Template is paused due to low quality…* · 132016 *Template is permanently disabled due to low quality.* |
| 130429 | *Cloud API message throughput has been reached.* · 131056 *Too many messages sent … to the same recipient … in a short period of time.* |

❌ **470 e 63016 NÃO existem na Cloud API** (On-Premises legado e Twilio, respectivamente) — o equivalente é 131047.

### 12.10 Campos de webhook assináveis (App Dashboard) — [doc](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/overview)
`account_alerts`, `account_review_update`, `account_update`, `automatic_events`, `business_capability_update`, `history`, `message_template_components_update`, `message_template_quality_update`, `message_template_status_update`, `messages`, `partner_solutions`, `payment_configuration_update`, `phone_number_name_update`, `phone_number_quality_update`, `security`, `smb_app_state_sync`, `smb_message_echoes`, `template_category_update`, `user_preferences`.
⚠️ **`message_echoes` não existe** — o nome real é `smb_message_echoes`. **`flows` não é** campo assinável de WABA. Assunto de **P5**.
