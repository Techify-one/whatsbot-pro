# Plano 143 — A avaliação do protocolo: parar de assinar como "IA" e parar de bater na janela de 24h

> **Status:** EXECUTADO (código) · **Renumerado:** era 142, que já estava tomado pelo plano da classificação de etapa comercial · **Data:** 2026-08-25 · **Escopo:** médio (1 arquivo de core + 3 do plugin `protocolos`; **zero migration**, zero mudança de contrato de canal)
> **Origem:** pedido do operador sobre a conversa `16145` do painel de produção — "esse link está sendo enviado em nome da IA… e em canais como Instagram e WhatsApp Oficial, passadas 24 h, ele tenta enviar e dá erro. Quero que o protocolo verifique antes, pra não ficar aparecendo um monte de erros nas minhas conversas."
> **Método:** leitura do código real com `arquivo:linha` verificados (core local + `protocolos` **2.5.0** de `origin/main` do repositório de plugins) + investigação somente-leitura no banco de produção pelo cofre de credenciais (a identificação da credencial fica fora deste documento — repositório público).
> **O quê/porquê:** são **dois defeitos independentes que se somam na mesma bolha**. (1) A avaliação é gravada como mensagem de operador (`status="operator"`), mas quando o provedor avisa a falha pelo webhook o `status` vira `"failed"` — e o painel decide o rótulo do remetente **só pelo status** ([MessageBubble.js:37](../web/static/js/components/contacts/MessageBubble.js#L37)), então toda falha de operador passa a assinar **"IA"**. (2) `send_protocol_on_close` entrega ao provedor **sem consultar a janela de 24 h**, embora o core já tenha o veredito pronto (`OutboundRouter.session_open`). Medido em 21 dias de produção: **648 de 1 008 avaliações falharam (64 %)**, e **641 dos 662** cards de erro do fio são o código `131047` ("passaram mais de 24 h").
>
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0 — Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 ✅ (2026-08-25, usuário) | Fora da janela: **pular o envio ao cliente e avisar por NOTA PRIVADA** no fio. | Nada de bolha vermelha, nada de card `role="error"`. Precedente vivo e reconhecível: o plugin `retornos` já escreve exatamente esse aviso (visto na própria conversa 16145, msg `689375`). |
| D2 ✅ (2026-08-25, usuário) | O remetente da avaliação passa a ser **`"Automação"`** — rótulo fixo, para padronizar as demais mensagens automáticas depois. | `sent_by_name="Automação"` no save. Não é o nome de quem finalizou: ninguém digitou aquele texto. |
| D3 ✅ (2026-08-25) | O veredito da janela é o do **core, por capability** — `OutboundRouter.session_open(channel_id, last_inbound_ts, by_human=True)` ([channels/outbound.py:48](../channels/outbound.py#L48)) — **não** uma regra de 24 h uniforme do plugin. | Prediz exatamente o que o provedor vai recusar. GOWA/Telegram/site (`session_window_hours=0`) seguem **sempre abertos** e não perdem uma avaliação sequer; Instagram/Messenger com `human_agent_tag` ligado ganham os 7 dias de graça. Copiar a regra fixa do `retornos` (§2.6) calaria a avaliação em canal onde ela funciona hoje. |
| D4 ✅ (2026-08-25) | A **nota privada de avaliação continua** sendo gravada mesmo com a janela fechada. | O gate cobre **só** o item 1 de `send_protocol_on_close` (o link que vai ao WhatsApp). O link privado é painel-only e nunca dependeu de janela nenhuma. Um `return` cedo — que é o desenho do gate de órfão e do de atributo — seria regressão. |
| D5 ✅ (2026-08-25) | Com o envio pulado, **`register_avaliacao` não roda**. | O token só serve para o link que o cliente recebeu; gravar linha para um link que ninguém viu suja `plugin_protocolos_avaliacoes` e o filtro "Nota de avaliação". |
| D6 ✅ (2026-08-25) | O rótulo "IA" numa falha de operador é **bug de CORE** e é corrigido no core, não contornado no plugin. | Não é específico do `protocolos`: **409** linhas `assistant/failed` em 7 dias, das quais só **7** têm `sent_by_name`. Toda mensagem manual de atendente que falha hoje é exibida como se a IA a tivesse escrito. Regra core-vs-plugin do `CLAUDE.md`: nenhum gancho existente enxerga isso, e é uma peça de UI do core. |
| D7 ✅ (2026-08-25) | A base do trabalho de plugin é o **`protocolos` 2.5.0** de `origin/main`, **não** a cópia local. | O checkout local instalou **1.35.1**; a branch local do repo de plugins está em **2.4.1**; **produção roda 2.5.0** e `origin/main` tem 2.5.0 na `src/`. Começar pela cópia local reverteria a 2.0.0 → 2.5.0 inteira. Ver §2.7 e a F0. |
| D8 ✅ (2026-08-25) | **Zero migration.** A nova opção é uma chave de config (`plugin.protocolos.protocol_*`), como as outras cinco da aba Avaliação. | Nada de schema. Nada de `plugin_protocolos_*` novo. |

**Princípio fixo:** o fio da conversa é o **registro do atendimento**, e mensagem que o provedor jamais entregaria não pertence a ele. Entre "tentar e errar visivelmente" e "não tentar e registrar por dentro", este plano escolhe sempre o segundo.

---

## 1 — Resumo executivo

A avaliação enviada ao finalizar um protocolo tem dois problemas que o operador vê como um só, porque acontecem na mesma bolha.

**O rótulo.** O plugin salva a avaliação como mensagem de operador. Quando o provedor devolve a falha pelo webhook, `mark_failed_by_msg_id` sobrescreve `status='operator'` por `'failed'` ([message_repo.py:692-695](../db/repositories/message_repo.py#L692-L695)) — e é **só o status** que o painel consulta para decidir de quem é a mensagem. Perdida a marca de operador, o `else` do rótulo é `"IA"`. O plugin agrava: nunca gravou `sent_by_name`, então nem no caminho feliz a bolha diz de onde veio (mostra `"Manual"`).

**A janela.** `send_protocol_on_close` chama `outbound.send_text` direto, sem consultar a janela de 24 h — apesar de o core já ter o veredito pronto e usado por todas as rotas de envio do painel. Como a Cloud API **aceita** o POST e só recusa depois, por webhook, o plugin recebe `ok=True`, grava `status='operator'` e a bolha só apodrece minutos depois, junto de um card de erro.

A correção é pequena e em duas camadas:

1. **Core** — o painel deixa de perder a identidade do remetente quando o status vira `failed` (D6), com o predicado extraído para um módulo puro já coberto por `node --test`.
2. **Plugin** — antes do envio ao cliente, consulta `session_open(..., by_human=True)`; fechada, grava nota privada de aviso (D1) em vez de entregar; e todo save passa a levar `sent_by_name="Automação"` (D2).

---

## 2 — Como funciona hoje (mapa verificado)

### 2.1 O caminho da avaliação, do clique em "Finalizar" à bolha vermelha

| # | Passo | Arquivo:linha (plugin = `protocolos` **2.5.0**) | Estado |
|---|---|---|---|
| 1 | Operador finaliza o protocolo | `POST /api/plugins/protocolos/protocolos/{atid}/close` — [routes.py:336](../storages/plugins/protocolos/routes.py#L336) | `uid, name = _atendente(request)` já disponível ([:337](../storages/plugins/protocolos/routes.py#L337)) |
| 2 | Dispara o envio em thread | [routes.py:361](../storages/plugins/protocolos/routes.py#L361) `await asyncio.to_thread(logic.send_protocol_on_close, at)` | best-effort, `except` engole tudo ([:362-363](../storages/plugins/protocolos/routes.py#L362-L363)) |
| 3 | Gates existentes | órfão ([logic.py:4954-4959](../storages/plugins/protocolos/logic.py#L4954)) e atributo ([logic.py:4962](../storages/plugins/protocolos/logic.py#L4962)) | **os dois fazem `return` — pulam TAMBÉM a nota privada** (ver D4) |
| 4 | **Nenhum gate de janela** | — | ⚠️ **é o buraco** |
| 5 | Registra o token | [logic.py:4972](../storages/plugins/protocolos/logic.py#L4972) `register_avaliacao(...)` | grava mesmo que o envio vá falhar |
| 6 | Entrega ao provedor | [logic.py:4979](../storages/plugins/protocolos/logic.py#L4979) `res = outbound.send_text(channel_id, phone, text_n)` | **`ok=True`** — a Meta aceita e devolve `wamid` |
| 7 | Salva a cópia do painel | [logic.py:4985-4988](../storages/plugins/protocolos/logic.py#L4985) `save_operator_message(..., status="operator" if ok else "failed", reopen=False)` | ⚠️ **sem `sent_by_name`** |
| 8 | Webhook da Meta: `statuses[].status="failed"` | [server/routes/channel_webhook.py](../server/routes/channel_webhook.py) | — |
| 9 | Status é sobrescrito | [message_repo.py:695](../db/repositories/message_repo.py#L695) `mark_failed_by_msg_id` | `'operator'` → `'failed'` (`_FAILABLE_STATUSES`, [:692](../db/repositories/message_repo.py#L692)) |
| 10 | Card de erro no fio | [channel_webhook.py:166-176](../server/routes/channel_webhook.py#L166-L176) `describe_failure(...)` | `role="error"` com o texto do `131047` ([message_errors.py:31-32](../server/message_errors.py#L31-L32)) |
| 11 | Painel escolhe o rótulo | [MessageBubble.js:37](../web/static/js/components/contacts/MessageBubble.js#L37) + [:57](../web/static/js/components/contacts/MessageBubble.js#L57) | `isOperator` é falso ⇒ **`"IA"`** |

⚠️ **O passo 9 é a armadilha central, e ela é assimétrica.** A função irmã `update_status_by_msg_id` **recusa de propósito** sobrescrever `'operator'` ([message_repo.py:634-637](../db/repositories/message_repo.py#L634-L637)) — é por isso que uma mensagem de atendente entregue e lida continua assinada com o nome dele. Só o caminho de **falha** destrói a autoria, e é exatamente onde o operador mais precisa saber quem tentou mandar o quê.

⚠️ **O passo 6 é a razão de nenhum `try/except` resolver isto.** O envio **não levanta e não devolve erro**: a Cloud API responde 200 com `wamid`, e só o webhook, segundos ou minutos depois, avisa que não entregou. O único remédio é **perguntar antes**.

### 2.2 O rótulo do remetente, linha a linha

```js
// web/static/js/components/contacts/MessageBubble.js
const isFailed   = m._status === 'failed' || m.status === 'failed';        // :35
const isOperator = !isUser && m.status === 'operator';                     // :37  ← perde a autoria
const aiLabel    = (showAgentName && m.agent_name) ? `IA - ${m.agent_name}` : 'IA';  // :54
const senderLabel = … (isOperator ? (m.sent_by_name || 'Manual') : aiLabel);          // :57
const sColor      = senderColor(isUser, isOperator);                                  // :58
```

`senderColor` ([messageView.js:201](../web/static/js/services/messageView.js#L201)) pinta operador de âmbar e IA de verde — ou seja, **a cor mente junto com o texto**. A mesma decisão está duplicada na citação de mensagem: [ContactDetail.js:738-739](../web/static/js/components/contacts/ContactDetail.js#L738-L739) repete `qmsg.status === 'operator' ? (qmsg.sent_by_name || 'Manual') : 'IA'`.

### 2.3 O que o core já tem pronto para a janela (e o plugin ignora)

| Peça | Arquivo:linha | O que faz |
|---|---|---|
| `OutboundRouter.session_open` | [channels/outbound.py:48-69](../channels/outbound.py#L48-L69) | veredito por **capability**; `by_human=True` soma `human_window_hours` |
| `OutboundRouter._window_open` | [channels/outbound.py:91-97](../channels/outbound.py#L91-L97) | `0 h ⇒ sempre aberta`; **sem inbound ⇒ fechada** |
| `message_repo.last_inbound_ts` | [db/repositories/message_repo.py:506](../db/repositories/message_repo.py#L506) | último `role='user'`, escopado por conversa |
| `session_window_block` | [app/services/messaging_service.py:160](../app/services/messaging_service.py#L160) | monta o veredito + a frase PT-BR, escolhida por capability de template |
| Consumidores no painel | [conversations.py:466](../server/routes/conversations.py#L466), [contacts.py:776](../server/routes/contacts.py#L776) | é o que bloqueia o compositor e imprime "Fora da janela de 24h…" |

O plugin já tem em mãos **as duas coisas de que precisa**, no mesmo escopo da função: `outbound` ([logic.py:4934](../storages/plugins/protocolos/logic.py#L4934)) e `conv_id` ([logic.py:4947](../storages/plugins/protocolos/logic.py#L4947)). O gate custa uma chamada a `last_inbound_ts` e uma a `session_open` — **nenhum import novo de módulo do core**.

### 2.4 As capabilities dos canais em produção (o gate por canal, medido)

| Canal (prod) | Provider | `session_window_hours` | `human_window_hours` | Efeito do gate |
|---|---|---|---|---|
| `Atendimento` | whatsapp_cloud | **24** ([channels.py:347](../storages/plugins/whatsapp_cloud/channels.py#L347)) | 0 | **é onde o defeito vive** |
| `whatsapp_oficial_disparo` | whatsapp_cloud | **24** | 0 | idem |
| `Instagram` | instagram **3.3.0** | **24** | **0** — `human_agent_tag=false` em prod (verificado) | idem, 24 h |
| `RedesBrasil_bot` | telegram | **0** ([channels.py:78](../storages/plugins/telegram/channels.py#L78)) | 0 | **nada muda** — sempre aberto |
| `Site` | website | **0** ([channels.py:61](../storages/plugins/website/channels.py#L61)) | 0 | **nada muda** |
| 3 canais GOWA | gowa | **0** | 0 | **nada muda** |

⚠️ **`human_window_hours` do `instagram` publicado é PROPERTY, não constante** ([whatsbot-pro-plugins `instagram/src/channels.py:218,257`], `docs/CANAIS_META.md` §Janela de 24h): devolve `0` com o toggle desligado e `168` com ele ligado. A cópia **instalada neste checkout** ainda é a antiga, com `24*7` fixo no `__init__` ([storages/plugins/instagram/channels.py:197](../storages/plugins/instagram/channels.py#L197)) — **não use o checkout local para raciocinar sobre a janela do Instagram**, e não "conserte" a property para uma constante.

### 2.5 O tamanho do problema (medido no banco de produção, 21 dias)

| Medida | Valor |
|---|---|
| Avaliações enviadas | **1 008** |
| Falharam (`status='failed'`) | **648 — 64 %** |
| Cards `role="error"` no fio, no período | **662** |
| … deles com o texto do `131047` ("mais de 24h") | **641 — 97 %** |
| Falhas de envio de QUALQUER origem, no período | **681** |
| … que são a avaliação do protocolo | **648 — 95 %** |
| Só no canal `Atendimento` | 953 enviadas, **635 falhas (67 %)** |
| Linhas `assistant` + `failed` em 7 dias | **409**, das quais só **7** têm `sent_by_name` |

**Leitura:** a avaliação do protocolo é responsável por **95 % de todo o ruído de falha de envio** da instalação. E dois terços dos clientes que o operador quis avaliar nunca receberam o link — o gate não perde uma entrega que hoje acontece; ele deixa de fingir 648 entregas que nunca aconteceram.

**Caso-testemunha (conversa `16145`, a do pedido):** último inbound do cliente em `2026-08-24 03:48`, avaliação enviada em `2026-08-25 12:10` — **≈32 h depois**. Linha `689465`: `role='assistant'`, `status='failed'`, `sent_by_name=NULL`, `msg_id='wamid.HBgMNTU2NTk5NjcwOTIx…'` (ou seja, **a Meta aceitou** e falhou depois). Card de erro `689466`, 1,7 s adiante.

### 2.6 O precedente: como o `retornos` já resolve isto

O plugin `retornos` faz exatamente a substituição que a D1 pede — e o operador **já reconhece o aviso**, porque ele aparece na própria conversa 16145 (msg `689375`, `sent_by_name="Retorno Automático"`):

> ⏳ A janela de 24 horas da Meta expirou para ~Flávio Macedo — o retorno automático NÃO foi enviado. Retome o contato manualmente (ou use um template aprovado).

| Aspecto | `retornos` ([actions.py `janela_aberta`](../storages/plugins/retornos/actions.py)) | **Este plano** |
|---|---|---|
| Predicado | 24 h fixas, **uniformes em todo canal** — abandonou `session_open` de propósito | **`session_open(..., by_human=True)`** (D3) |
| Por quê a diferença | o objetivo dele é **não incomodar** o cliente frio, mesmo onde o envio funcionaria | o objetivo daqui é **não produzir erro** — só faz sentido onde o provedor recusa |
| Fora da janela | nota privada de aviso | **igual** (D1) |
| Opção de desligar | `respeitar_janela_24h`, por configuração | **igual** — nova chave na aba Avaliação (F5) |
| `sent_by_name` | `"Retorno Automático"` | `"Automação"` (D2) |

⚠️ **Não unifique os dois predicados.** Aplicar a regra fixa do `retornos` aqui calaria a avaliação nos 5 canais sempre-abertos da instalação, onde ela hoje é entregue sem uma falha sequer (Telegram: 16 envios, 0 falhas).

### 2.7 ⚠️ Onde está o código do `protocolos` (três cópias, três versões diferentes)

| Lugar | Versão | Serve para |
|---|---|---|
| `../whatsbot-pro-plugins` `origin/main` → `plugins/protocolos/src/` | **2.5.0** | ✅ **é a base deste plano** (D7) |
| `../whatsbot-pro-plugins` **branch local** `main` | 2.4.1 | atrás do remoto — precisa de `git pull` (F0) |
| `storages/plugins/protocolos/` (este checkout) | **1.35.1** | cópia instalada, **muito atrás** — não é fonte de nada |
| Produção | **2.5.0** | o que o operador está vendo |

Os `arquivo:linha` de plugin deste documento são os da **2.5.0**. A diferença entre 1.35.1 e 2.5.0 na função em questão é **só de numeração de linha** — a lógica de `send_protocol_on_close` é idêntica byte a byte nas duas (verificado), mas o resto do arquivo mudou muito (2.0.0 reformou a tela de configuração; 2.2.0–2.5.0 acrescentaram a API interna de serviços).

### 2.8 Falsos positivos descartados

| Suspeita | Veredito | Prova |
|---|---|---|
| "A IA está escrevendo a avaliação" | ❌ **Não.** O plugin nunca chama LLM nesse caminho; a linha nasce `status='operator'`. O "IA" é rótulo de tela, aplicado depois. | [logic.py:4985](../storages/plugins/protocolos/logic.py#L4985) + linha `689465` (`sent_by_name=NULL`, `status='failed'`) |
| "O `send_text` devolveu erro e o plugin ignorou" | ❌ **Não.** Devolveu `ok=True` com `wamid` — a recusa vem depois, por webhook. Nenhum `try/except` no plugin veria isso. | `msg_id` presente na linha `689465` |
| "Basta trocar para `MessagingService.send_text`, que já tem o gate" | ❌ **Tentador e errado.** Ela resolve `reopen` por `filter.conversation.before_reopen` ([messaging_service.py:625-628](../app/services/messaging_service.py#L625-L628)) em vez de `reopen=False`, então a avaliação **reabriria** o atendimento recém-fechado e o jogaria de volta em "Abertas"; e chama `abort_ai_cycle` ([:661](../app/services/messaging_service.py#L661)). Regressão de comportamento em troca de reuso. | [logic.py:4987](../storages/plugins/protocolos/logic.py#L4987) comenta o `reopen=False` como decisão explícita |
| "O `update_status_by_msg_id` também apaga a autoria (entregue/lida)" | ❌ **Não.** Recusa sobrescrever `'operator'` de propósito. Só o caminho de falha destrói. | [message_repo.py:634-637](../db/repositories/message_repo.py#L634-L637); em prod, 2 049 de 2 210 linhas `operator` têm nome, contra 7 de 409 `failed` |
| "É o alerta `131047` do `whatsapp_cloud` enchendo a tela" | ❌ **Outro assunto.** Aquele grupo é alerta de Telegram e vem **OFF por padrão**. O ruído da queixa é o card `role="error"` do fio. | `docs/CANAIS_META.md` §Catálogo de grupos |
| "O plugin `janela_72h` deveria cuidar disso" | ❌ **Outra janela.** Ele trata a janela de free-entry point de anúncio CTWA, não o gate de envio. | `docs/CANAIS_META.md` |
| "É preciso migration para a nova opção" | ❌ **Não.** As 5 chaves da aba Avaliação já são `config_repo` com prefixo `plugin.protocolos.protocol_*`. | [logic.py:4663-4690](../storages/plugins/protocolos/logic.py#L4663-L4690) |

---

## 3 — Mudanças por camada

### 3.1 Core (frontend) — 2 arquivos

| Alvo | Mudança | Risco |
|---|---|---|
| [messageView.js:201](../web/static/js/services/messageView.js#L201) | **novo** helper puro `isOperatorMessage(m)` ao lado de `senderColor` — `status === 'operator'` **ou** (falhou **e** tem `sent_by_user_id`/`sent_by_name`). Módulo já coberto por `node --test`. | baixo |
| [MessageBubble.js:37](../web/static/js/components/contacts/MessageBubble.js#L37) · [ContactDetail.js:738-739](../web/static/js/components/contacts/ContactDetail.js#L738-L739) | passam a usar o helper em vez de repetir `status === 'operator'`. | baixo |

⚠️ **O predicado tem de exigir a marca de autoria, não só `isFailed`.** Uma resposta da **IA** que falha também tem `status='failed'` — sem a segunda metade da condição ela passaria a assinar "Manual", trocando um rótulo errado por outro. É por isso que a F4 (o plugin gravar `sent_by_name`) e a F1 (o core respeitá-lo) **se completam**: sozinha, a F1 não conserta a bolha da avaliação; sozinha, a F4 conserta só o caminho feliz.

### 3.2 Plugin `protocolos` — 3 arquivos (base 2.5.0)

| Alvo | Mudança |
|---|---|
| `logic.py` [:4920-5010](../storages/plugins/protocolos/logic.py#L4920) `send_protocol_on_close` | gate de janela **entre** o gate de atributo ([:4962](../storages/plugins/protocolos/logic.py#L4962)) e o `register_avaliacao` ([:4972](../storages/plugins/protocolos/logic.py#L4972)); `sent_by_name`/`sent_by_user_id` nos dois saves; nota privada de aviso quando fechada |
| `logic.py` [:4663-4690](../storages/plugins/protocolos/logic.py#L4663) `get/set_protocol_config` | 2 chaves novas: `respeitar_janela` (default **True**) e `avisar_janela_fechada` (default **True**) |
| `static/config.js` [:583](../storages/plugins/protocolos/static/config.js#L583) `renderAvaliacao` | 2 caixas de seleção na aba Avaliação, com dica ⓘ (padrão da 2.0.0) |
| `plugin.yaml` | bump para **2.6.0** + parágrafo de release (MINOR: comportamento novo, sem quebra) |

**Sem mudança de `WHATSBOT_API_VERSION`:** nada de novo é acrescentado ao catálogo do bus, a nenhum símbolo público nem a `entry`. O plugin passa a **consumir** `outbound_router.session_open`, que existe desde a API 1.0 e ele já tem em mãos.

⚠️ **Import defensivo obrigatório.** `message_repo.last_inbound_ts` **não** é superfície versionada (`CLAUDE.md`: "`db.repositories` fica de fora de propósito"). O plugin já importa `message_repo` no topo, mas o gate inteiro tem de degradar para **"janela aberta"** (fail-open, comportamento de hoje) se qualquer peça faltar — nunca calar a avaliação por um `AttributeError`.

---

## 4 — Fases e paralelização

```
WAVE 0   F0(base 2.5.0) 🔴  ·  F1(core: rótulo) 🟢  ·  F2(caracterização) 🟢
            │ (barreira: F0 bloqueia TODA fase de plugin)
WAVE 1   F3(gate da janela) 🔴 ─┬─ [depende de: F0]
         F4(identidade "Automação") 🟢 ─┘
WAVE 2   F5(tela: 2 opções) 🟢  ·  F6(testes do plugin) 🟢   [dependem de: F3, F4]
WAVE 3   F7(build + zip + instalar) 🔴  →  F8(docs) 🟢
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | repo de plugins | 🔴 | baixo | `src/plugin.yaml` diz `2.5.0` e `git status` limpo · **[bloqueia: F3, F4, F5, F6, F7]** |
| 0 | **F1** | core / frontend | 🟢 | baixo | `node --test` verde; bolha de operador que falhou mostra o nome, não "IA" |
| 0 | **F2** | testes (core) | 🟢 | baixo | teste de caracterização **vermelho** provando o rótulo errado antes da F1 |
| 1 | **F3** | plugin / logic | 🔴 | **médio** | fora da janela: nota privada, zero bolha, zero card de erro · **[depende de: F0]** |
| 1 | **F4** | plugin / logic | 🟢 | baixo | bolha entregue assina **"Automação"** · **[depende de: F0]** |
| 2 | **F5** | plugin / config.js | 🟢 | baixo | 2 caixas na aba Avaliação, legíveis no modo escuro · **[depende de: F3]** |
| 2 | **F6** | testes (plugin) | 🟢 | baixo | `scripts/test_plugins.py --python-only protocolos` verde |
| 3 | **F7** | build / deploy | 🔴 | **médio** | `--check` byte a byte; zip instalado em prod; **primeiro fechamento fora da janela sem card de erro** |
| 3 | **F8** | docs | 🟢 | baixo | guias atualizados; `test_docs_hygiene` verde |

---

### Fase F0 — Sincronizar a base do plugin (2.4.1 local → 2.5.0 do remoto)

**Objetivo:** garantir que toda edição de plugin parta do que **produção roda** (D7).

**Itens**
1. `[sequencial]` `cd ../whatsbot-pro-plugins && git pull` — a branch local está em `2.4.1` (`49ae820`), `origin/main` em **2.5.0** (`9f951be`).
2. `[sequencial]` Confirmar `grep -m1 '^version:' plugins/protocolos/src/plugin.yaml` ⇒ `2.5.0`.
3. `[sequencial]` Confirmar contra o banco de produção que a tabela `plugins` diz `protocolos = 2.5.0`. ⚠️ Memória do repositório: **uma versão pode ser publicada por outra pessoa no meio do trabalho** — se prod já estiver acima de 2.5.0, **pare e reconcilie antes de editar**.
4. `[sequencial]` **Não** usar `storages/plugins/protocolos/` (1.35.1) como referência para nada.

**Pronto quando:** `src/plugin.yaml` = `2.5.0`, árvore limpa, e a versão de prod confirmada igual.

#### Status de execução — Fase F0
**Estado:** ✅ Concluída
- **O que foi feito:** Nada de `git pull`. Verificado o estado real do repositório de plugins e a versão de produção (tabela `plugins` = `protocolos 2.5.0`, confirmado).
- **Como foi feito / decisões:** ⚠️ **A premissa da fase estava desatualizada.** O `HEAD` local é de fato o `2.4.1`, mas a **árvore de trabalho** tem WIP **não commitado** de outra frente que já contém a 2.5.0 inteira **mais** uma 2.6.0 em andamento (`atualizar_campos_protocolo`, para a rotina de IA de classificação — o plano 142). Verificado com `git diff origin/main -- plugins/protocolos/src/`: as únicas diferenças para o remoto são adições da 2.6.0; a 2.5.0 está toda lá. Ou seja, a árvore é **superconjunto** do que produção roda, que é o que a D7 queria garantir. Um `git pull` sobre uma árvore suja arriscaria conflito ou perda desse WIP sem ganho nenhum — a base foi mantida como está.
- **Problemas / pendências:** ⚠️ **O trabalho deste plano ficou entrelaçado com o WIP da 2.6.0 no mesmo arquivo (`logic.py`).** Consequência direta na F7: **gerar o zip agora empacotaria também a 2.6.0 não terminada**. Por isso a versão foi para **2.7.0** (a 2.6.0 já está reivindicada) e a publicação **não foi executada** — decisão do usuário.
- **Verificação:** `git diff origin/main -- plugins/protocolos/src/` (só adições da 2.6.0) · consulta somente-leitura à tabela `plugins` de produção.

---

### Fase F1 — Core: a falha de envio deixa de apagar a autoria (D6)

**Objetivo:** mensagem de operador que falhou continua sendo do operador, no texto e na cor.

**Itens**
1. `[sequencial]` Em [messageView.js](../web/static/js/services/messageView.js) (ao lado de `senderColor`, [:201](../web/static/js/services/messageView.js#L201)), exportar um predicado puro — assinatura ilustrativa:
   ```js
   export function isOperatorMessage(m) // true p/ status 'operator' OU (falhou E tem sent_by_user_id/sent_by_name)
   ```
   ⚠️ **A segunda metade da condição é obrigatória** (§3.1): sem ela, uma resposta da IA que falha passa a assinar "Manual".
   ⚠️ Cobrir os **dois** campos de falha que o painel usa: `m.status === 'failed'` **e** `m._status === 'failed'` (o otimista do compositor) — [MessageBubble.js:35](../web/static/js/components/contacts/MessageBubble.js#L35).
2. `[paralelo]` [MessageBubble.js:37](../web/static/js/components/contacts/MessageBubble.js#L37) passa a chamar o helper; `senderLabel` ([:57](../web/static/js/components/contacts/MessageBubble.js#L57)) e `senderColor` ([:58](../web/static/js/components/contacts/MessageBubble.js#L58)) não mudam de forma.
3. `[paralelo]` [ContactDetail.js:738-739](../web/static/js/components/contacts/ContactDetail.js#L738-L739) (rótulo/cor da **citação**) passa a chamar o mesmo helper — hoje é uma 2ª cópia da regra.
4. `[sequencial]` Casos no [messageView.test.js](../web/static/js/services/messageView.test.js) (`node --test`): operador entregue ⇒ operador; operador **falhado com nome** ⇒ operador; **IA falhada** ⇒ IA; `user` ⇒ nunca operador.

⚠️ **Nada de backend nesta fase.** Não mexa em `mark_failed_by_msg_id` nem em `_FAILABLE_STATUSES` ([message_repo.py:692](../db/repositories/message_repo.py#L692)): o `status` é o estado de **entrega**, e `failed` é o estado correto. Quem estava lendo autoria de um campo de entrega era o painel.

**Pronto quando:** `node --test web/static/js/services/messageView.test.js` verde; no painel, uma mensagem manual do atendente que falhou mostra o **nome dele** em âmbar (hoje mostra "IA" em verde); a resposta da IA que falha continua "IA".

#### Status de execução — Fase F1
**Estado:** ✅ Concluída
- **O que foi feito:** Novo predicado puro `isOperatorMessage(m)` em [messageView.js](../web/static/js/services/messageView.js), ao lado de `senderColor`. Os dois call sites passaram a chamá-lo: [MessageBubble.js:37](../web/static/js/components/contacts/MessageBubble.js#L37) (a bolha) e [ContactDetail.js:736-741](../web/static/js/components/contacts/ContactDetail.js#L736-L741) (o rótulo e a cor da citação), que eram duas cópias da mesma regra.
- **Como foi feito / decisões:** Condição composta, como a §3.1 exige — `status === 'operator'` **ou** (falhou **e** tem `sent_by_user_id`/`sent_by_name`). Cobre os dois campos de falha (`status` e o otimista `_status`). Mensagem do cliente sai por `role === 'user'` antes de qualquer outra checagem, e entrada `null`/vazia devolve `false`. Zero mudança no backend: `failed` é o estado de entrega correto.
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `node --test web/static/js/services/messageView.test.js` — **33 passed**. `node --input-type=module --check` nos dois componentes.

---

### Fase F2 — Caracterização ANTES (disciplina do repo)

**Objetivo:** provar os dois defeitos com teste **vermelho** antes de corrigi-los.

**Itens**
1. `[paralelo]` `node --test`: linha `{role:'assistant', status:'failed', sent_by_name:'Fulano'}` ⇒ hoje resolve para "IA". **Vermelho** até a F1.
2. `[paralelo]` Python, no repositório de plugins, ao lado de [test_evaluation_skip_conditions.py](../storages/plugins/protocolos/tests/python/test_evaluation_skip_conditions.py): `send_protocol_on_close` com canal `session_window_hours=24` e último inbound de 30 h atrás ⇒ hoje **chama** `outbound.send_text`. **Vermelho** até a F3. Usar o `build_app([...])` que os testes do plugin já usam.

**Pronto quando:** os dois testes existem, falham pelo motivo certo, e a mensagem de falha descreve o comportamento **desejado**.

#### Status de execução — Fase F2
**Estado:** ✅ Concluída
- **O que foi feito:** 6 casos novos em [messageView.test.js](../web/static/js/services/messageView.test.js) e o arquivo novo `tests/python/test_evaluation_window.py` no repositório de plugins (13 casos).
- **Como foi feito / decisões:** A disciplina foi respeitada: os testes de `node --test` foram escritos e rodados **antes** da F1 e falharam pelo motivo certo (o helper não existia). Os de Python foram escritos junto da F6 — a fase F3 mudou a estrutura da função (a variável de decisão), então um teste de caracterização escrito contra a forma antiga teria de ser reescrito de qualquer jeito.
- **Problemas / pendências:** Nenhuma.
- **Verificação:** vermelho antes da F1 (`ERR_TEST_FAILURE` no import), verde depois.

---

### Fase F3 — O gate da janela em `send_protocol_on_close` 🔴

**Objetivo:** não entregar ao provedor o que ele vai recusar; registrar o motivo por dentro (D1).

**Itens**
1. `[sequencial]` Novo helper em `logic.py`, ao lado de `_is_orphan_protocolo` ([:4781](../storages/plugins/protocolos/logic.py#L4781)) — assinatura ilustrativa:
   ```python
   def _evaluation_window_open(outbound, channel_id, conversation_id) -> bool:
       """Fail-OPEN: qualquer erro/peça ausente ⇒ True (comportamento de hoje)."""
   ```
   Corpo: `message_repo.last_inbound_ts(conversation_id=…)` → `outbound.session_open(channel_id, last_ts, by_human=True)` (D3).
   ⚠️ **`by_human=True` é a metade que importa** — sem ele, um Instagram/Messenger com `human_agent_tag` ligado seria pulado no 2º dia embora o envio funcionasse por 7 (`docs/CANAIS_META.md`).
   ⚠️ **Fail-open, não fail-closed.** O `retornos` fecha na dúvida porque o mal dele é incomodar o cliente; aqui o mal é **não avaliar quem podia ser avaliado**.
2. `[sequencial]` Chamar o gate em `send_protocol_on_close` **depois** de `_should_skip_evaluation` ([:4962](../storages/plugins/protocolos/logic.py#L4962)) e **antes** de `register_avaliacao` ([:4972](../storages/plugins/protocolos/logic.py#L4972)) — a ordem é contrato (D5): sem envio, sem token.
3. `[sequencial]` ⚠️ **Não use `return`.** Os dois gates vizinhos retornam cedo e por isso pulam **também** a nota privada; este precisa pular **só o bloco 1** ([:4977-4991](../storages/plugins/protocolos/logic.py#L4977)) e deixar o bloco 2 ([:4993](../storages/plugins/protocolos/logic.py#L4993)) rodar (D4). Uma variável de decisão, não uma saída.
4. `[sequencial]` Aviso por nota privada quando fechada (D1) — `role="private_note"`, mesmo caminho do bloco 2 (`cm.add_message` + `broadcast("new_message", …)` com `channel_id`), `sent_by_name="Automação"`. Texto na linha do que o operador já reconhece: *"⏳ A janela de 24 horas deste canal expirou — a mensagem de avaliação NÃO foi enviada."*
   ⚠️ **Nunca `role="error"`** e nunca uma bolha: o pedido inteiro é **remover** cards de erro do fio.
5. `[sequencial]` Respeitar as chaves da F5: `respeitar_janela` desligada ⇒ gate inerte (envia como hoje); `avisar_janela_fechada` desligada ⇒ pula sem nota.
6. `[sequencial]` `logger.info` no pulo, com `protocolo_id` + `channel_id` — é o que permite medir depois.

**Pronto quando:** finalizar um protocolo com último inbound > 24 h num canal WhatsApp Cloud **não** produz bolha, **não** produz card `role="error"` e deixa **uma** nota privada; a nota privada de link interno (bloco 2), se configurada, **continua aparecendo**; num canal GOWA/Telegram o envio acontece exatamente como antes; teste da F2 fica verde.

#### Status de execução — Fase F3
**Estado:** ✅ Concluída
- **O que foi feito:** Em `logic.py`: novo `_evaluation_window_open(outbound, channel_id, conversation_id)` (vizinho de `_is_orphan_protocolo`); a variável de decisão `window_open` em `send_protocol_on_close`, entre o gate de atributo e o `register_avaliacao`; o bloco 1b da nota privada de aviso; e o novo `_save_private_note(...)`, extraído do bloco 2 e agora usado pelos dois.
- **Como foi feito / decisões:** `session_open(..., by_human=True)` (D3), com `message_repo.last_inbound_ts` importado **local e defensivamente** (não é superfície versionada). **Fail-open** em todo erro, com `logger.warning`. Não é `return` (D4): só o bloco 1 é pulado, e o `register_avaliacao` ficou condicionado a `window_open` (D5). O texto do aviso segue o do plugin `retornos`, que o operador já reconhece. `logger.info` no pulo, com `protocolo_id` + `channel_id`.
  Desvio pequeno: em vez de duplicar o corpo da nota privada para o aviso, o bloco 2 foi **extraído** para `_save_private_note` — os dois passaram a compartilhar o cuidado com o `channel_id` (a nota tem de cair no mesmo atendimento do mesmo canal) em vez de ter duas cópias dele.
- **Problemas / pendências:** Nenhuma.
- **Verificação:** 13 testes em `test_evaluation_window.py`, verdes.

---

### Fase F4 — Identidade "Automação" (D2)

**Objetivo:** a avaliação assina de onde veio, no caminho feliz e no de falha.

**Itens**
1. `[paralelo]` Constante de módulo em `logic.py` (ex. `AUTOMATION_SENDER = "Automação"`) — um lugar só, para a padronização futura pedida pelo usuário.
2. `[paralelo]` `save_operator_message` ([:4985-4988](../storages/plugins/protocolos/logic.py#L4985)) passa a levar `sent_by_name=AUTOMATION_SENDER`. Assinatura já aceita ([agent/handler.py:381-390](../agent/handler.py#L381-L390)).
3. `[paralelo]` `sent_by_user_id` fica **`None`**: não existe usuário "Automação", e a coluna é FK para `users` ([db/tables.py:148-152](../db/tables.py#L148-L152)). O snapshot de nome é justamente o campo que sobrevive sem usuário.
4. `[paralelo]` Mesmo nome nas notas privadas deste caminho (a do bloco 2 e a nova da F3) — hoje a do bloco 2 sai **sem nome**, e o `SystemMessageCard` já sabe imprimir "· por &lt;nome&gt;" ([SystemMessageCard.js:82](../web/static/js/components/contacts/SystemMessageCard.js#L82)).
5. `[paralelo]` **Não** mexer no `params["assignee_id"]` da URL ([:4944-4945](../storages/plugins/protocolos/logic.py#L4944)) — é o atendente do protocolo para a página de avaliação, outro assunto.

**Pronto quando:** uma avaliação entregue mostra **"Automação"** em âmbar (não "Manual", não "IA"); combinada com a F1, uma avaliação que falhe por outro motivo (ex. `131026`, número inexistente) **também** mostra "Automação".

#### Status de execução — Fase F4
**Estado:** ✅ Concluída
- **O que foi feito:** Constante `AUTOMATION_SENDER = "Automação"` em `logic.py`; `sent_by_name=AUTOMATION_SENDER` no `save_operator_message` do bloco 1 e no `add_message` das duas notas privadas (via `_save_private_note`).
- **Como foi feito / decisões:** `sent_by_user_id` fica `None` — não existe usuário "Automação" e a coluna é FK para `users`. Um lugar só para a constante, como a P2 pede para a padronização futura. `params["assignee_id"]` não foi tocado.
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `test_envio_assina_automacao` verde (nome correto **e** `sent_by_user_id is None`).

---

### Fase F5 — As duas opções na aba Avaliação

**Objetivo:** o operador pode desligar o gate e/ou o aviso, sem tocar em código.

**Itens**
1. `[sequencial]` `get_protocol_config` ([:4663](../storages/plugins/protocolos/logic.py#L4663)) e `set_protocol_config` ([:4678](../storages/plugins/protocolos/logic.py#L4678)) ganham `respeitar_janela` e `avisar_janela_fechada`, **default `True`** nos dois.
   ⚠️ **Grave só quando a chave estiver presente no payload** — é o padrão já usado em `set_general_config` ([logic.py:4478-4481](../storages/plugins/protocolos/logic.py#L4478-L4481)) para um payload antigo não zerar um default.
2. `[paralelo]` `renderAvaliacao` ([config.js:583](../storages/plugins/protocolos/static/config.js#L583)) ganha as 2 caixas, com o ⓘ da 2.0.0 (explicação em dica, não em parágrafo solto). `PROTO_EMPTY` ([config.js:259](../storages/plugins/protocolos/static/config.js#L259)) ganha os dois campos.
3. `[paralelo]` Modo escuro: classes `wa-*` e `.wa-field`; **testar com o tema escuro ligado** (regra do `CLAUDE.md`).
4. `[paralelo]` A tela **é** a configuração do plugin — nada disso encosta no `ConfigPanel.js` do core.

**Pronto quando:** as caixas salvam e recarregam pelo `PUT /protocol-config`; desmarcar "respeitar a janela" devolve o comportamento atual (envia e falha); legível nos dois temas.

#### Status de execução — Fase F5
**Estado:** ✅ Concluída
- **O que foi feito:** `get_protocol_config`/`set_protocol_config` ganharam `respeitar_janela` e `avisar_janela_fechada` (default `True`); `PROTO_EMPTY` e `renderAvaliacao` ganharam um `Card` "Janela de 24 horas do canal" com as duas caixas.
- **Como foi feito / decisões:** A escrita só acontece quando a chave vem no payload — um cliente antigo não zera o default. A segunda caixa é **aninhada** (só aparece com a primeira ligada), no mesmo padrão do "Tempo mínimo para a IA reassumir" da aba Geral. Classes `wa-*` e componentes `Card`/`CheckRow`/`SectionTitle` existentes, então o tema escuro vem de graça. Nada encostou no `ConfigPanel.js` do core.
- **Problemas / pendências:** ⏸️ **A validação visual no modo escuro não foi feita** — depende de instalar o plugin, que a F7 deixou pendente.
- **Verificação:** `test_defaults_ligados_e_payload_antigo_nao_zera` verde · `node --input-type=module --check` no `config.js`.

---

### Fase F6 — Testes do plugin

**Objetivo:** travar as regras que este plano criou, para nenhuma delas voltar em silêncio.

**Itens** — novo `tests/python/test_evaluation_window.py` (harness igual ao [test_evaluation_skip_conditions.py](../storages/plugins/protocolos/tests/python/test_evaluation_skip_conditions.py)):

| # | Caso | Esperado |
|---|---|---|
| 1 | canal 24 h, inbound de 30 h atrás | **não** envia; **uma** nota privada de aviso |
| 2 | canal 24 h, inbound de 1 h atrás | envia normalmente |
| 3 | canal `session_window_hours=0` (GOWA/Telegram), inbound de 30 dias | **envia** (⚠️ o caso que impede a regressão do §2.6) |
| 4 | `by_human=True` chega ao `session_open` | canal com `human_window_hours=168` no 2º dia **envia** |
| 5 | janela fechada | `register_avaliacao` **não** é chamado (D5) |
| 6 | janela fechada + link privado configurado | a nota privada do bloco 2 **continua** sendo gravada (D4) |
| 7 | `outbound=None` / `last_inbound_ts` levanta | **fail-open** — envia |
| 8 | `respeitar_janela=False` | gate inerte, envia |
| 9 | `avisar_janela_fechada=False` | pula sem nota |
| 10 | envio bem-sucedido | save leva `sent_by_name="Automação"` |

**Pronto quando:** `python3 scripts/test_plugins.py --python-only protocolos` verde, e `--all` sem regressão.

#### Status de execução — Fase F6
**Estado:** ✅ Concluída
- **O que foi feito:** `plugins/protocolos/tests/python/test_evaluation_window.py`, 13 casos cobrindo os 10 da tabela.
- **Como foi feito / decisões:** O harness isola a **decisão**: dublês de `outbound` (com a mesma aritmética de janela do core, inclusive o `by_human`) e de `agent_handler`, e `monkeypatch` nos vizinhos que já têm suíte própria (órfão, atributo, resolução de canal). Assim o teste mede o que este plano criou, e não a montagem de um protocolo.
- **Problemas / pendências:** ⚠️ Uma armadilha encontrada e corrigida: a config vive na tabela `config`, que **sobrevive ao fim do teste** (o schema é recriado uma vez por processo, não por teste) — o teste de configuração deixava a avaliação LIGADA para todos os testes seguintes. Agora ele restaura o estado num `finally`.
- **Verificação:** `python3 scripts/test_plugins.py --python-only protocolos` — **255 passed, 0 failed**.
  ⚠️ **Três rodadas antes disso deram 4, depois 21 falhas — e nenhuma era do código.** O banco de teste (`WHATSBOT_TEST_DB_URL`) estava sendo usado ao mesmo tempo por **outra máquina** (`10.8.200.102`, visível em `pg_stat_activity`), e cada processo recria o mesmo schema `public`. Os mesmos testes passavam isolados. A rodada válida foi a que esperou o banco ficar livre antes de começar. Não diagnostique falha de suíte aqui sem olhar `pg_stat_activity` primeiro.

---

### Fase F7 — Build, publicação e instalação 🔴

**Objetivo:** o que roda em produção é o que foi testado.

**Itens**
1. `[sequencial]` `plugin.yaml`: `2.5.0` → **`2.6.0`** + parágrafo de release no padrão do arquivo.
2. `[sequencial]` `python3 scripts/build_plugins.py protocolos` e depois `--check`.
   ⚠️ **`--check` mente por `umask`** (memória do repositório): zip com modo 664 aparece como "outdated" mesmo idêntico. **Não rebuild "para consertar"** — investigue o modo antes.
   ⚠️ **`--check` compara zip × src e não vê arquivo que sumiu da fonte** — foi assim que a 1.26.0 saiu sem `retornos_fields.py`. Confira que os 4 arquivos tocados estão no zip.
3. `[sequencial]` Antes de publicar, **reconferir a versão de prod** (tabela `plugins` + `audit_log`): outra pessoa pode ter publicado no meio do caminho.
4. `[sequencial]` Instalar no **ambiente local** e fechar um protocolo de verdade — memória do repositório: commit/zip não muda o que roda; a cópia viva é `storages/plugins/<id>/`.
5. `[sequencial]` Commit + push no repositório de plugins; `Importar (.zip)` em produção.
6. `[sequencial]` O deploy do **core** (F1) é `git push` normal — sem zip.
   ⚠️ **Ordem:** o core pode ir antes ou depois; as duas metades são independentes e nenhuma quebra sem a outra (a F1 sozinha não conserta a avaliação, mas não regride nada).

**Pronto quando:** prod diz `protocolos = 2.6.0`; o primeiro protocolo finalizado fora da janela deixa **nota privada e nenhum card de erro**; nenhuma linha `assistant/failed` com `content LIKE 'AVALIE%'` nas 24 h seguintes.

#### Status de execução — Fase F7
**Estado:** ⛔ Bloqueada
- **O que foi feito:** Só o bump: `plugin.yaml` `2.6.0` → **`2.7.0`** com o parágrafo de release. **Nada foi buildado, commitado, publicado ou instalado.**
- **Como foi feito / decisões:** A versão pulou a 2.6.0 porque ela já está reivindicada pelo WIP não commitado descrito na F0.
- **Problemas / pendências:** ⛔ **Bloqueada por decisão do usuário, não por defeito.** `build_plugins.py` empacota a árvore de trabalho inteira, e ela contém a 2.6.0 de outra frente, **em andamento e não testada por esta sessão**. Gerar e publicar o zip agora entregaria esse trabalho a produção junto. As opções são: (a) terminar/reverter a 2.6.0 antes de buildar; (b) isolar este plano numa branch/worktree só com os 4 arquivos tocados aqui. Precisa da decisão de quem é dono do WIP.
  O deploy do **core** (F1) é independente e não está bloqueado: é `git push` normal, sem zip.
- **Verificação:** _(pendente)_

---

### Fase F8 — Documentação

**Objetivo:** a regra dura fica onde quem for mexer vai olhar.

**Itens**
1. `[paralelo]` [docs/UI_CONVERSA.md](../docs/UI_CONVERSA.md): o rótulo do remetente sai de `isOperatorMessage` (módulo puro), **não** de `status === 'operator'`; ⚠️ falha de envio é estado de **entrega** e não pode apagar autoria; ⚠️ o predicado exige a marca de autoria, senão a IA vira "Manual".
2. `[paralelo]` [docs/CANAIS_META.md](../docs/CANAIS_META.md), na seção da janela de 24 h: quem envia por fora das rotas do painel (plugin) **tem de consultar `session_open(..., by_human=True)` antes** — a Cloud API aceita o POST e só recusa por webhook, então `ok=True` não é prova de entrega.
3. `[paralelo]` [docs/PLUGINS.md](../docs/PLUGINS.md): precedente do gate de janela em plugin (`retornos` uniforme × `protocolos` por capability) e **por que são diferentes de propósito**.
4. `[sequencial]` `CLAUDE.md`: **no máximo ~2 linhas** — a regra e o ⚠️. Sugestão, na seção do painel de conversa: *"⚠️ Rótulo do remetente vem de `isOperatorMessage`, não de `status==='operator'`: a falha de envio sobrescreve `operator`→`failed` e fazia toda mensagem manual falhada assinar 'IA'."* O resto vai para os guias.
   ⚠️ O arquivo tem teto travado por [tests/contracts/test_docs_hygiene.py](../tests/contracts/test_docs_hygiene.py) (plano 139) — rode-o depois.

**Pronto quando:** `venv/bin/python -m pytest tests/contracts/test_docs_hygiene.py` verde e os 3 guias carregam o porquê completo.

#### Status de execução — Fase F8
**Estado:** ✅ Concluída
- **O que foi feito:** [docs/UI_CONVERSA.md](../docs/UI_CONVERSA.md) — nova seção "De quem é a bolha: autoria NÃO sai do estado de entrega". [docs/CANAIS_META.md](../docs/CANAIS_META.md) — ⚠️ na seção da janela de 24h, sobre quem envia por fora das rotas do painel. [docs/PLUGINS.md](../docs/PLUGINS.md) — nova subseção comparando os predicados do `retornos` e do `protocolos` e por que são diferentes de propósito. `CLAUDE.md` — **uma** linha, na seção do painel de conversa.
- **Como foi feito / decisões:** O orçamento do `CLAUDE.md` foi respeitado (regra + ⚠️, o resto nos guias).
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `venv/bin/python -m pytest tests/contracts/test_docs_hygiene.py` — **2 passed**.

---

## 5 — Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Predicado do rótulo (F1) | Incluir `isFailed` sem exigir autoria faz a **IA** assinar "Manual" — troca um rótulo errado por outro | Condição composta + caso de teste dedicado (§3.1, F1 item 4) |
| Gate no lugar errado (F3) | `return` cedo mataria também a nota privada do link interno | D4 + item 3 da F3 + teste 6 da F6 |
| Predicado uniforme (F3) | Copiar a regra fixa do `retornos` calaria a avaliação em 5 canais sempre-abertos | D3 + **teste 3 da F6** (GOWA com inbound de 30 dias **envia**) |
| Fail-closed por engano (F3) | `session_open` devolve `False` quando **não há inbound nenhum** ([outbound.py:95](../channels/outbound.py#L95)) | O helper é **fail-open** só para erro/peça ausente; "sem inbound em canal com janela" é fechado **de propósito** — não há janela aberta para reaproveitar |
| `human_window_hours` (F3) | Esquecer `by_human=True` pula avaliação que o Instagram entregaria por 7 dias | Item 1 da F3 + **teste 4 da F6** |
| Cópia local do `instagram` (§2.4) | Raciocinar sobre a janela do IG pela cópia instalada (constante `24*7`) em vez da publicada (property) | Usar `whatsbot-pro-plugins/plugins/instagram/src/` como referência; nunca "consertar" a property |
| Base errada (F0) | Editar sobre 1.35.1 ou 2.4.1 desfaz 2.0.0–2.5.0 | D7 + F0 bloqueante |
| Versão publicada por terceiro | Alguém publica 2.6.0 no meio do trabalho e o `git fetch` local não vê | Conferir `plugins` + `audit_log` de prod na F0 **e** na F7 |
| `--check` do build (F7) | Falso "outdated" por `umask`; cegueira a arquivo removido | Item 2 da F7 |
| Restart de plugin (F7) | `Importar (.zip)`/toggle derruba o processo (`os._exit`) | Supervisor é requisito conhecido; janela de baixo movimento |
| Segredo em URL | O link de avaliação leva `assignee_id` + `id_protocol` | **Nada muda aqui** — este plano não altera `_append_query` nem o formato do link |
| Modo escuro (F5) | Caixas novas ilegíveis no tema escuro | Classes `wa-*`; teste visual obrigatório |
| Postgres | — | Zero migration (D8); nenhuma consulta nova além de `last_inbound_ts`, que é indexada e já roda em toda abertura de conversa |
| Volume de notas privadas (D1) | ~30 notas/dia a mais no fio (estimativa a partir dos 641 erros/21 dias) | É a decisão do usuário; `avisar_janela_fechada` desliga sem tocar em código (F5) |

---

## 6 — Perguntas em aberto

**P1 — Fora da janela: pular, avisar ou mandar template?**
✅ **DECIDIDO (2026-08-25, usuário):** **pular + nota privada de aviso** (D1). O template HSM recuperaria os ~64 % de avaliações não entregues, mas exige template aprovado na Meta, custo por mensagem e uma fase inteira a mais. Fica como candidato a **plano futuro**, não como parte deste.

**P2 — Que nome assina a avaliação?**
✅ **DECIDIDO (2026-08-25, usuário):** **"Automação"** — *"vou padronizar com os outros no futuro"* (D2). Consequência prática: a constante mora num lugar só (F4 item 1), para uma futura passada padronizar `retornos` ("Retorno Automático") e demais automações sem caçar literais.

**P3 — Corrigir o rótulo no core ou contornar no plugin?**
✅ **DECIDIDO (2026-08-25):** **core** (D6). Não é defeito do `protocolos`: **409** falhas de operador em 7 dias, das quais só 7 têm nome — toda mensagem manual de atendente que falha hoje é exibida como se a IA a tivesse escrito. Nenhum gancho de plugin enxerga rótulo de bolha, e é peça de UI do core.

**P4 — O que fazer com as 648 bolhas e 641 cards de erro que JÁ estão no histórico?**
⏸️ **ADIADO — decisão do usuário, fora deste plano.**
(a) deixar como estão (histórico é histórico — é o padrão do repositório e o que este plano assume);
(b) limpeza retroativa das linhas `role='error'` com código `131047` cuja mensagem-alvo é a avaliação.
**Recomendação: (a).** Depois da F1 essas bolhas deixam de assinar "IA" sozinhas se o `sent_by_name` existir — mas as **antigas** têm `sent_by_name=NULL` e continuarão em "IA", porque não há de onde tirar a autoria. Um `UPDATE` de backfill (`sent_by_name='Automação'` onde `content LIKE 'AVALIE%' AND status='failed'`) é possível e barato, mas é reescrever histórico: só com pedido explícito e backup antes.

**P5 — A avaliação não entregue deveria ser re-tentada quando o cliente voltar a falar?**
⏸️ **ADIADO.** Fecharia o buraco de negócio de verdade (o cliente avalia quando reabre a janela), mas exige estado persistido (`plugin_protocolos_*` + migration) e um gancho em `message.saved`. Fora do escopo — a queixa era o ruído, não a taxa de avaliação. **Anotar como candidato**, junto do template do P1.

---

## 7 — Apêndice: arquivos-chave

**Core — frontend (F1)**
- [web/static/js/services/messageView.js](../web/static/js/services/messageView.js) — `senderColor` [:201](../web/static/js/services/messageView.js#L201); **novo** `isOperatorMessage`
- [web/static/js/services/messageView.test.js](../web/static/js/services/messageView.test.js) — `node --test`
- [web/static/js/components/contacts/MessageBubble.js](../web/static/js/components/contacts/MessageBubble.js) — [:35](../web/static/js/components/contacts/MessageBubble.js#L35), [:37](../web/static/js/components/contacts/MessageBubble.js#L37), [:57-58](../web/static/js/components/contacts/MessageBubble.js#L57-L58)
- [web/static/js/components/contacts/ContactDetail.js](../web/static/js/components/contacts/ContactDetail.js) — [:738-739](../web/static/js/components/contacts/ContactDetail.js#L738-L739)

**Plugin `protocolos` 2.5.0 → 2.6.0** (em `../whatsbot-pro-plugins/plugins/protocolos/src/`, **nunca** em `storages/`)
- `logic.py` — `send_protocol_on_close` [:4920](../storages/plugins/protocolos/logic.py#L4920); `get/set_protocol_config` [:4663](../storages/plugins/protocolos/logic.py#L4663)/[:4678](../storages/plugins/protocolos/logic.py#L4678); `_is_orphan_protocolo` [:4781](../storages/plugins/protocolos/logic.py#L4781) (vizinho do helper novo)
- `static/config.js` — `renderAvaliacao` [:583](../storages/plugins/protocolos/static/config.js#L583); `PROTO_EMPTY` [:259](../storages/plugins/protocolos/static/config.js#L259)
- `plugin.yaml` — bump + release note
- `tests/python/test_evaluation_window.py` — **novo**

**Core — só leitura (contrato consumido, não alterado)**
- [channels/outbound.py](../channels/outbound.py) — `session_open` [:48](../channels/outbound.py#L48), `_window_open` [:91](../channels/outbound.py#L91)
- [db/repositories/message_repo.py](../db/repositories/message_repo.py) — `last_inbound_ts` [:506](../db/repositories/message_repo.py#L506), `mark_failed_by_msg_id` [:695](../db/repositories/message_repo.py#L695)
- [app/services/messaging_service.py](../app/services/messaging_service.py) — `session_window_block` [:160](../app/services/messaging_service.py#L160) (referência de frase/desenho)
- [agent/handler.py](../agent/handler.py) — `save_operator_message` [:381](../agent/handler.py#L381)
- [server/routes/channel_webhook.py](../server/routes/channel_webhook.py) — `_emit_failure_card` [:166](../server/routes/channel_webhook.py#L166)
- [server/message_errors.py](../server/message_errors.py) — `131047` [:31](../server/message_errors.py#L31)

**Docs (F8)** — [docs/UI_CONVERSA.md](../docs/UI_CONVERSA.md) · [docs/CANAIS_META.md](../docs/CANAIS_META.md) · [docs/PLUGINS.md](../docs/PLUGINS.md) · `CLAUDE.md` (≤ 2 linhas)

---

## 8 — Checklist de verificação

- [x] `node --test web/static/js/services/messageView.test.js` verde (F1/F2) — **33 passed**; os 32 arquivos de `services/` verdes
- [x] `venv/bin/python -m pytest` — **só as 3 falhas pré-existentes conhecidas** (cadeia do Alembic ×2 + matriz de auditoria). Este plano não tem migration nem evento de auditoria. ⚠️ O concorrente ESTAVA em outra máquina nesta sessão (`10.8.200.102`) e produziu 4 e depois 21 falhas fantasmas — `pg_stat_activity` antes de culpar o código
- [x] `tests/contracts` verde dentro da suíte completa; `test_docs_hygiene.py` rodado à parte (**2 passed**). O golden da API de plugins não foi tocado — nada novo no catálogo
- [x] `python3 scripts/test_plugins.py --python-only protocolos` — **255 passed, 0 failed** · ⏸️ `--all` não rodado (o repositório tem WIP não commitado de outras frentes)
- [ ] ⏸️ (depende da F7) Painel: mensagem manual do atendente que falhou mostra o **nome dele** em âmbar, não "IA" em verde
- [ ] ⏸️ (depende da F7) Painel: avaliação entregue assina **"Automação"**
- [ ] ⏸️ (depende da F7) Painel: resposta da **IA** que falha continua assinando **"IA"** (o caso que o predicado composto protege)
- [ ] ⏸️ (depende da F7) Fechamento fora da janela em canal WhatsApp Cloud: **nota privada, zero bolha, zero card de erro**
- [ ] ⏸️ (depende da F7) Fechamento fora da janela: a nota privada do **link interno** continua aparecendo (D4)
- [ ] ⏸️ (depende da F7) Fechamento em canal GOWA/Telegram com inbound antigo: **envia normalmente** (nada regrediu)
- [ ] ⏸️ (depende da F7) Aba Avaliação: as 2 caixas salvam, recarregam e são legíveis no **modo escuro**
- [ ] ⏸️ (depende da F7) `respeitar_janela` desmarcada devolve exatamente o comportamento de hoje
- [x] Zero migration nova em `db/alembic/versions/` e em `plugins/protocolos/src/migrations/`
- [x] Nenhum segredo novo em URL; `_append_query` e o formato do link não foram tocados
- [ ] ⏸️ (depende da F7) `build_plugins.py --check` limpo (⚠️ conferir modo do arquivo antes de rebuildar)
- [ ] ⏸️ (depende da F7) Restart de plugin ok após `Importar (.zip)` (supervisor relançou)
- [ ] ⏸️ (depende da F7) Prod, 24 h depois: **zero** linha `role='assistant' AND status='failed' AND content LIKE 'AVALIE%'`


---

## 9 — Estado final da execução (2026-08-25)

**Código pronto e verde nas duas metades; a publicação do plugin ficou bloqueada.**

| Camada | Estado | Onde |
|---|---|---|
| Core (rótulo do remetente) | ✅ pronto, testado | 4 arquivos neste checkout — deploy é `git push` normal |
| Plugin `protocolos` (gate + identidade + tela) | ✅ pronto, testado | 4 arquivos em `../whatsbot-pro-plugins/plugins/protocolos/`, versão **2.7.0** |
| Publicação do plugin | ⛔ **bloqueada** | ver a F0 e a F7 |
| Documentação | ✅ pronta | 3 guias + 1 linha no `CLAUDE.md` |

⛔ **O bloqueio, em uma frase:** a árvore do repositório de plugins tem WIP **não commitado** de outra frente (a 2.6.0, `atualizar_campos_protocolo`, do plano 142) **no mesmo `logic.py`**, e o `build_plugins.py` empacota a árvore inteira — publicar agora entregaria esse trabalho a produção junto com este. Testes, tela e comportamento deste plano estão prontos; falta separar as duas frentes (terminar/reverter a 2.6.0, ou isolar este plano num worktree só com os 4 arquivos daqui).

**Verificado em produção antes de começar (somente leitura):** `plugins.protocolos = 2.5.0` — a base assumida pela D7 estava correta e ninguém publicou no meio do caminho.

⚠️ **Este plano foi renumerado de 142 para 143 durante a execução.** O 142 já pertencia ao plano da classificação de etapa comercial por IA, e já estava citado como "plano 142" em `docs/PLUGINS.md`, `docs/IA.md` e `docs/PLUGIN_API_CHANGELOG.md`. Todas as referências deste trabalho (código, testes, guias) dizem **143**.
