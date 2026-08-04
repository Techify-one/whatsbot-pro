# Plano 95 — Descartar no plugin oficial a mensagem que a Meta entrega SEM conteúdo (`unsupported` / 131051)

> **Status:** PLANEJAMENTO · **Data:** 2026-07-30 · **Escopo:** pequeno
> **Origem:** investigação do caso real do contato `447974905044` (remetente de código 2FA do Facebook) na conversa 855 de produção, em 2026-07-30 11:08 e 12:34. **Método:** leitura do payload cru no `debug_bus_1785431496831.jsonl` (linhas 3703–3707), do caminho de ingestão do core e do plugin `whatsapp_cloud` 1.8.0, + consulta ao banco de produção via MCP Vault, tudo com `arquivo:linha` verificado.
> A Meta entrega no webhook uma mensagem **sem corpo nenhum** (`type: "unsupported"`, `errors[0].code = 131051`, título `Message type unknown`). O core hoje trata isso como fala do cliente: **reabre o atendimento, marca não-lida, abre protocolo, acorda a IA e gasta token** — para uma bolha que não diz nada. Este plano descarta essas mensagens **dentro do plugin do WhatsApp Oficial**, por TIPO/código do erro (nunca por regex do texto), com **zero alteração no core**.
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| D1 ✅ (2026-07-30) | A regra casa pelo **título/tipo da mensagem**, não por regex do texto | A chave é `type == "unsupported"` + `errors[].code`; o `title` (`Message type unknown`) entra só como rótulo humano no log/config — nunca como critério de casamento |
| D2 ✅ (2026-07-30) | Não abrir **nada**: sem atendimento, sem protocolo, sem IA, sem badge de não-lida | O corte tem que acontecer **antes** do core ingerir o evento — ver §2.3, que prova que qualquer gancho posterior chega tarde demais para o badge |
| D3 ✅ (2026-07-30) | Tudo fica **só no plugin `whatsapp_cloud`** | Nenhum arquivo fora de `assets/plugin_examples/whatsapp_cloud/` (+ o zip e os testes). Isso **exclui deste plano** a variante "manter a bolha no fio sem acionar nada", que exigiria o core (ver P1) |
| D4 ✅ (2026-07-30) | Não existe recuperação do conteúdo | O payload chega vazio da origem; nada a implementar nesse eixo (§2.1). O plano trata só o ruído |
| D5 ✅ (2026-07-30) | **Descartar TODO `unsupported`**, não só o código 131051 (resposta ao P3: *"pode ser tudo"*) | `ignore_error_codes` nasce **vazio** = qualquer `unsupported`. A lista continua existindo só como estreitamento futuro, sem mudar código. ⚠️ Consequência aceita: enquete/view-once de cliente real também some — o `logger.warning` vira o único rastro |
| D6 ✅ (2026-07-30) | Config **global do plugin**, não por canal (resposta ao P2) | Uma chave em `settings.py` vale para os dois canais Cloud (`Atendimento` inbox 21 e `whatsapp_oficial_disparo` inbox 18). Sem campo novo no `provider_descriptor` |
| D7 ✅ (2026-07-30) | **Não** reescrever o texto do fallback `describe_unsupported` (resposta ao P4) | Fica em inglês/como está. Com D5 esse texto praticamente nunca renderiza para `unsupported`, então mexer nele seria código sem efeito prático |
| D8 ✅ (2026-07-30) | Limpar só a sobra do incidente; **sem backfill** do histórico (resposta ao P5) | Fechar o protocolo `#15353` e o atendimento `855` na UI (F6.4). As 4 linhas `unsupported` já salvas ficam como estão — apagar mexeria em histórico de conversa por ganho zero |

---

## 1. Resumo executivo

Desde o cutover do número `556299071262` para a Cloud API (**primeira mensagem nativa em 2026-07-20 08:03**, inbox 21), os códigos de 2FA que a Meta manda para esse número chegam **vazios**: a plataforma recusa entregar o corpo e o webhook traz só `type: "unsupported"` + `errors[0].code = 131051`. O WhatsBot, que não tem como saber que aquilo não é uma fala de cliente, roda o pipeline inteiro em cima disso.

Custo medido de **duas** dessas mensagens em um único dia: 1 atendimento reaberto, 1 protocolo aberto (`#15353`), 4 respostas da IA enviadas para um sistema da Meta, 1 `transfer_to_human` e **US$ 0,0325** em token.

A correção é **um `continue`** no lugar certo: o `parse_inbound` do plugin — que é quem enxerga o `errors[].code` e é o **primeiro** código a tocar a mensagem — deixa de emitir o `InboundEvent`. Sem evento, o core não materializa nada, o `message.saved` nunca sai e o plugin `protocolos` sequer é chamado. O rastro forense continua intacto porque o payload cru é capturado **antes** do parse (§2.4).

---

## 2. Como funciona hoje (mapa)

### 2.1 O que a Meta entrega (payload real de produção)

`debug_bus_1785431496831.jsonl:3703` — íntegra do item em `messages[]`:

```json
{"from": "447974905044", "from_user_id": "GB.2076874439870767",
 "id": "wamid.HBgMNDQ3OTc0OTA1MDQ0FQIAEhgSQzk5Q0I4Q0E3MUI2RDYwNkRBAA==",
 "timestamp": "1785420528",
 "errors": [{"code": 131051, "title": "Message type unknown",
             "message": "Message type unknown",
             "error_data": {"details": "Message type is currently not supported."}}],
 "type": "unsupported", "unsupported": {"type": "unknown"}}
```

⚠️ **Não há corpo, mídia, template nem legenda.** O texto que aparece na bolha (`⚠️ Mensagem não suportada pelo WhatsApp Business (unknown): Message type unknown`) é **gerado pelo próprio plugin** em `describe_unsupported` ([inbound_text.py:288](../assets/plugin_examples/whatsapp_cloud/inbound_text.py#L288)) — é por isso que **casar esse texto por regex seria acoplar regra de negócio a string de UI**: reescrever a frase quebraria a regra em silêncio (D1).

Marcadores estáveis disponíveis, em ordem de confiabilidade:

| Campo | Valor no caso real | Estabilidade |
|---|---|---|
| `msg["type"]` | `"unsupported"` | Contrato do webhook da Meta — **usar como chave** |
| `msg["errors"][0]["code"]` | `131051` | Código documentado da Meta — **usar como chave** |
| `msg["errors"][0]["title"]` | `"Message type unknown"` | Rótulo humano, pode mudar de redação/locale — **só para log/UI** |
| `msg["unsupported"]["type"]` | `"unknown"` | Subtipo; útil no log |
| texto renderizado | `⚠️ Mensagem não suportada…` | **Gerado por nós** — nunca usar como chave |

### 2.2 O caminho de hoje, ponto a ponto

| # | Etapa | Onde | O que acontece com a mensagem vazia |
|---|---|---|---|
| 1 | Meta → `POST /api/webhook/whatsapp_cloud/{channel_id}` | [channel_webhook.py:716-728](../server/routes/channel_webhook.py#L716-L728) | Payload cru registrado e passado ao provider |
| 2 | `parse_inbound` | [channels.py:1006](../assets/plugin_examples/whatsapp_cloud/channels.py#L1006) | Itera `messages[]` ([:1043-1048](../assets/plugin_examples/whatsapp_cloud/channels.py#L1043-L1048)) e chama `_parse_message` **para todo item, sem exceção** |
| 3 | `_parse_message`, ramo `else` | [channels.py:1183-1188](../assets/plugin_examples/whatsapp_cloud/channels.py#L1183-L1188) | Vira `media_type="unsupported"`, `media_extras={"unsupported_type":…}` e texto de aviso |
| 4 | `_dispatch_events` → `ingest_event` | [channel_webhook.py:727](../server/routes/channel_webhook.py#L727) | Entra no pipeline normal de mensagem |
| 5 | **Contato materializado** | [message_ingest_service.py:410](../app/services/message_ingest_service.py#L410) | Cria a linha em `contacts` se o remetente for novo |
| 6 | **Badge de não-lida** | [message_ingest_service.py:429](../app/services/message_ingest_service.py#L429) | `increment_unread` |
| 7 | `filter.message.before_save` | [message_ingest_service.py:455](../app/services/message_ingest_service.py#L455) | Único ponto de "descarte" oferecido a plugins — **e já é tarde** (5 e 6 passaram) |
| 8 | **Atendimento criado/reaberto** | [message_ingest_service.py:482](../app/services/message_ingest_service.py#L482) `ensure_conversation_live` | Conversa 855 reaberta (`conversation.reopened`, log:3705) |
| 9 | Batch salva + `message.saved` | [messaging_service.py:891-925](../app/services/messaging_service.py#L891-L925) | Emite o evento que o `protocolos` assina |
| 10 | **Protocolo aberto** | [protocolos/events.py:27](../storages/plugins/protocolos/events.py#L27) → `logic.on_inbound` | `#15353` / `PROT-20260730-140855-15353` |
| 11 | **IA responde** | pipeline agêntico | 2 execuções, US$ 0,0325, `transfer_to_human` |

### 2.3 ⚠️ Por que `filter.message.before_save` NÃO resolve

É o gancho óbvio e ele **cumpre parte** do pedido (mata save, atendimento, `message.saved`, protocolo e IA), mas roda na [linha 455](../app/services/message_ingest_service.py#L455) — **depois** de:

- [:410](../app/services/message_ingest_service.py#L410) o contato já ter sido materializado (remetente novo ⇒ contato fantasma), e
- [:429](../app/services/message_ingest_service.py#L429) o `increment_unread` já ter rodado ⇒ **badge contando uma mensagem que nunca vai existir**.

Corrigir isso seria reordenar o core — vetado por D3. Cortar no `parse_inbound` (etapa 2) contorna as duas armadilhas **sem tocar em nada do core**, porque simplesmente não existe evento para ingerir.

### 2.4 O que NÃO se perde ao descartar

| Rastro | Onde | Sobrevive? |
|---|---|---|
| Payload cru completo | `filter.webhook.payload` (plugin `debug_bus`, ativo em produção) | ✅ — roda **antes** do `parse_inbound` |
| Payload cru completo | `GET /api/channel-webhook-payloads` ([channel_webhook.py:730](../server/routes/channel_webhook.py#L730)) | ✅ — mesmo motivo |
| Sinal operacional ("a Meta tentou entregar algo") | — | ⚠️ vira o `logger.warning` da F2 |
| Conteúdo da mensagem | — | ❌ nunca existiu (§2.1 / D4) |

### 2.5 Estado das cópias do plugin (⚠️ ler antes de editar)

| Lugar | Versão | Observação |
|---|---|---|
| `assets/plugin_examples/whatsapp_cloud/` | 1.8.0 | **Fonte no git — é aqui que se edita** |
| `storages/plugins/whatsapp_cloud/` | 1.8.0 | Cópia instalada no dev. `plugin.yaml` diverge só no bloco de `description` (8 linhas a menos); pasta `tests/` vazia |
| `assets/channel_plugins/whatsapp_cloud-plugin.zip` | — | Zip importável, regravado em 2026-07-30 14:02 |
| Produção (`plugins.version` no banco) | **1.8.0** | Sem drift para este plugin (ao contrário do `telegram`, que roda 1.2.2 fora do git) |

---

## 3. Inventário do que fazer

| # | Item | Arquivo | O que falta | Risco | Esforço |
|---|---|---|---|---|---|
| I1 | Módulo puro de decisão | `assets/plugin_examples/whatsapp_cloud/inbound_ignore.py` (**novo**) | `should_ignore(msg, codes) -> bool` + `describe_ignored(msg) -> str`. Stdlib-only, sem import do core, sem I/O | Baixo | S |
| I2 | Gancho no parse | [channels.py:1043-1048](../assets/plugin_examples/whatsapp_cloud/channels.py#L1043-L1048) | `continue` antes de `_parse_message` quando ignorável, com `logger.warning` | Baixo | S |
| I3 | Import defensivo | [channels.py:224-228](../assets/plugin_examples/whatsapp_cloud/channels.py#L224-L228) | Mesmo `try/except` do `describe_message` — zip antigo tem que continuar carregando | Baixo | S |
| I4 | Config no próprio plugin | [settings.py](../assets/plugin_examples/whatsapp_cloud/settings.py) | `ignore_empty_meta_messages: bool = True` + `ignore_error_codes: str = "131051"` | Baixo | S |
| I5 | Leitura da config + cache | `channels.py` (novo helper) | `config_repo.get("plugin.whatsapp_cloud.*")` com TTL ~30s — precedente no próprio plugin em [routes.py:72](../assets/plugin_examples/whatsapp_cloud/routes.py#L72) | Médio | S |
| I6 | Testes puros | `tests/test_whatsapp_cloud_ignore_empty.py` (**novo**) | Carregar `inbound_ignore.py` **por path**, no molde de [test_plano75_cloud_inbound_text.py:20-34](../tests/test_plano75_cloud_inbound_text.py#L20-L34) | Baixo | S |
| I7 | Teste de integração | `tests/test_endpoints.py` ou arquivo próprio | POST do payload real no webhook ⇒ `events == 0`, nenhuma linha em `messages`/`atendimentos`, `unread` intacto | Médio | M |
| I8 | Bump + zip + deploy | [plugin.yaml](../assets/plugin_examples/whatsapp_cloud/plugin.yaml) 1.8.0 → 1.9.0 | Regravar `assets/channel_plugins/whatsapp_cloud-plugin.zip` e reimportar em produção | Baixo | S |
| I9 | Limpeza operacional | Produção (UI) | Fechar o protocolo `#15353` e o atendimento `855`, deixados abertos pelo incidente | Baixo | S |

### 3.1 Falsos positivos descartados

| "Problema" aparente | Por que NÃO é |
|---|---|
| "Dá para mostrar o código de 2FA se tratarmos melhor o payload" | **Não.** O payload não tem corpo (§2.1) e a Cloud API não expõe endpoint de "buscar mensagem recebida por id". Nada a implementar |
| "Use `filter.message.before_save` que já existe" | Resolve 5 dos 6 sintomas, mas roda **depois** do contato e do `increment_unread` (§2.3) — o badge continuaria |
| "Precisa de uma regex na regra 'ignorar abertura' do `protocolos`" | Funcionaria hoje (ela já pluga em reopen + IA + notify + protocolo, [logic.py:3174](../storages/plugins/protocolos/logic.py#L3174)), mas casa **texto gerado por nós** — quebra em silêncio na primeira reescrita da frase (D1). Além disso poria regra de canal dentro do plugin de protocolo |
| "Precisa mexer no plugin `protocolos`" | **Não.** Ele assina `message.saved` ([events.py:27](../storages/plugins/protocolos/events.py#L27)); sem evento de mensagem, o handler nunca é chamado |
| "Precisa desligar a IA explicitamente" | **Não.** Sem `InboundEvent` não há batch, não há LLM, não há custo |
| "Precisa de migration" | **Não.** Settings de plugin vivem em `config` sob `plugin.whatsapp_cloud.*` |
| "Precisa de RBAC / auditoria" | **Não.** É descarte de tráfego de conversa — e CLAUDE.md proíbe explicitamente pôr tráfego de mensagem na trilha de auditoria |
| "Vai afetar os recibos (`statuses[]`) do mesmo remetente" | **Não.** O corte é só no laço de `messages[]` ([:1043](../assets/plugin_examples/whatsapp_cloud/channels.py#L1043)); `statuses[]` é outro laço ([:1051](../assets/plugin_examples/whatsapp_cloud/channels.py#L1051)) |
| "Vai afetar GOWA/Telegram" | **Não.** A mudança está inteira dentro de `whatsapp_cloud` (D3) |
| "Perde-se o rastro do incidente" | **Não** — §2.4 |

---

## 4. A regra (o miolo)

```python
# inbound_ignore.py — puro, stdlib-only, sem core, sem I/O
DEFAULT_IGNORED_ERROR_CODES: tuple[int, ...] = ()   # D5 — vazio = TODO ``unsupported``

def should_ignore(msg: dict, codes: tuple[int, ...] = DEFAULT_IGNORED_ERROR_CODES) -> bool:
    """True quando a Meta entregou a mensagem SEM conteúdo algum."""
```

| Condição | Decisão | Por quê |
|---|---|---|
| `msg["type"] != "unsupported"` | **Passa** | ⚠️ **A âncora é o tipo literal `"unsupported"`.** Um tipo NOVO e nomeado (ex.: a Meta passar a entregar `"poll"`) pode vir **com** payload — continua passando e caindo no ramo `else` de hoje |
| `type == "unsupported"` **e** `codes` vazio (**default, D5**) | **Descarta** | Qualquer mensagem sem conteúdo, independente do código |
| `type == "unsupported"` **e** algum `errors[].code` ∈ `codes` | **Descarta** | Só quando o operador estreitou a lista na config |
| `type == "unsupported"` **e** `codes` preenchido, código fora da lista | **Passa** | Estreitamento explícito do operador |
| `type == "unsupported"` **sem** `errors` | **Descarta** com `codes` vazio · **Passa** com `codes` preenchido | Sem `errors` não há o que casar contra uma lista |
| `msg` não é dict / `errors` malformado | **Passa** | Fail-open: erro na regra **nunca** engole mensagem de cliente |
| Toggle `ignore_empty_meta_messages = False` | **Passa** | Volta ao comportamento atual sem redeploy |

**Fail-open é obrigatório**: qualquer exceção dentro de `should_ignore` (ou no helper de config) é capturada no call site e a mensagem **passa**. O modo de falha aceitável é "voltou o ruído"; o inaceitável é "sumiu mensagem de cliente".

**Log obrigatório** no descarte (é o único rastro em texto):

```
[whatsapp_cloud] mensagem sem conteúdo descartada — canal=%s de=%s wamid=%s code=%s title=%s
```

---

## 5. Fases

```
WAVE 0   F1(módulo puro + regra) ──────────────────────  🔴 sozinha (define a assinatura)
              │ (barreira: F2, F3 e F4 dependem da assinatura de F1)
WAVE 1   F2(gancho no parse) · F3(settings+leitura) · F4(testes puros)   🟢 as três juntas
              │
WAVE 2   F5(teste de integração)                         🔴 [depende de: F2, F3]
              │
WAVE 3   F6(bump + zip + deploy + limpeza)               🔴 release
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | F1 | `inbound_ignore.py` (novo) | 🔴 | Baixo | `should_ignore` cobre as 6 linhas da tabela §4 e não importa nada do core |
| 1 | F2 | Gancho em `parse_inbound` + import defensivo | 🟢 [depende de: F1] | Baixo | Payload real ⇒ `parse_inbound` devolve `[]` e loga o warning |
| 1 | F3 | `settings.py` + leitura com cache | 🟢 [depende de: F1] | Médio | Toggle aparece em Configurar; desligar volta o comportamento antigo sem restart |
| 1 | F4 | `tests/test_whatsapp_cloud_ignore_empty.py` | 🟢 [depende de: F1] | Baixo | Verde via `node`-style puro (`pytest`, sem DB) |
| 2 | F5 | Teste de integração do webhook | 🔴 [depende de: F2, F3] | Médio | POST do payload real ⇒ zero linha nova em `contacts`/`messages`/`atendimentos` e `unread` intacto |
| 3 | F6 | `plugin.yaml` 1.9.0 + zip + import em prod + limpeza do 15353/855 | 🔴 [depende de: F4, F5] | Baixo | Card mostra 1.9.0 e a próxima tentativa da Meta não gera nada |

### F1 — Módulo puro da regra (🔴)

1. Criar `assets/plugin_examples/whatsapp_cloud/inbound_ignore.py`, **stdlib-only** — mesmo contrato de pureza de [inbound_text.py](../assets/plugin_examples/whatsapp_cloud/inbound_text.py), que é carregado por path nos testes.
2. `DEFAULT_IGNORED_ERROR_CODES: tuple[int, ...] = ()` (**D5** — vazio = todos) + `parse_codes(raw: str) -> tuple[int, ...]` (aceita `"131051, 131052"`, ignora lixo, devolve `()` quando vazio).
3. `should_ignore(msg, codes)` conforme §4, com `try/except Exception: return False` no corpo inteiro.
4. `describe_ignored(msg) -> str` devolvendo `code=… title=… subtype=…` para o log — é aqui, e **só** aqui, que o `title` é lido.

#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-07-30)
- **O que foi feito:** criado `assets/plugin_examples/whatsapp_cloud/inbound_ignore.py` (novo, único arquivo da fase) com `DEFAULT_IGNORED_ERROR_CODES: tuple[int, ...] = ()`, `UNSUPPORTED_TYPE = "unsupported"`, `parse_codes(raw)`, `should_ignore(msg, codes)` e `describe_ignored(msg)`.
- **Como foi feito / decisões:** stdlib-only, zero import (nem do core, nem de `httpx`) — o módulo só decide; log e `continue` ficam no call site (F2). A âncora é o literal `msg["type"] == "unsupported"`, comparado por igualdade exata (tipo NOVO e nomeado continua passando). `codes` vazio ⇒ descarta qualquer `unsupported` (D5). `parse_codes` aceita também `list/tuple` e `;` como separador (tolerância barata, sem custo), deduplica preservando ordem e devolve `()` para vazio/lixo. `try/except Exception: return False` envolve o corpo inteiro das três funções — fail-open. `describe_ignored` é o ÚNICO lugar que lê `title` (cai em `message` quando não há `title`), com placeholder `?` por campo ausente.
- **Problemas / pendências:** nenhum.
- **Verificação:** exercício manual via `venv/bin/python` carregando o módulo por path — payload real de §2.1 ⇒ `True` (com `codes=()` e com `(131051,)`), `False` com `(999,)`; texto ⇒ `False`; `None`/`"x"`/`errors="x"` ⇒ `False`; `parse_codes` devolve `(131051,131052)`/`()`/`()`/`(131051,)`; `describe_ignored` ⇒ `code=131051 title=Message type unknown subtype=unknown`. Bateria formal em F4.

### F2 — Gancho no `parse_inbound` (🟢)

1. `[sequencial]` Import defensivo no topo de `channels.py`, colado no bloco do `describe_message` ([:224-228](../assets/plugin_examples/whatsapp_cloud/channels.py#L224-L228)):
   ```python
   try:
       from .inbound_ignore import should_ignore, parse_codes, describe_ignored
   except Exception:  # noqa: BLE001 — zip antigo sem o módulo novo
       def should_ignore(msg, codes=()): return False   # fail-open
   ```
2. `[sequencial]` No laço de `messages[]` ([:1043-1048](../assets/plugin_examples/whatsapp_cloud/channels.py#L1043-L1048)), antes do `self._parse_message(...)`: se `should_ignore(msg, codes)` ⇒ `logger.warning(...)` (formato do §4) + `continue`.
3. `[sequencial]` **Não** tocar no laço de `statuses[]` ([:1051](../assets/plugin_examples/whatsapp_cloud/channels.py#L1051)) nem no ramo `else` do `_parse_message` ([:1183-1188](../assets/plugin_examples/whatsapp_cloud/channels.py#L1183-L1188)) — este continua sendo o fallback dos `unsupported` que **não** casam a regra.

**Pronto quando:** `parse_inbound` alimentado com o payload de §2.1 devolve `[]`, e alimentado com o mesmo payload + uma mensagem de texto na mesma `messages[]` devolve **só** a de texto.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-07-30)
- **O que foi feito:** em `assets/plugin_examples/whatsapp_cloud/channels.py` — (a) import defensivo de `should_ignore`/`parse_codes`/`describe_ignored`/`DEFAULT_IGNORED_ERROR_CODES` colado no bloco do `describe_message`, com fallback no-op; (b) no laço de `messages[]` do `parse_inbound`, `logger.warning(...)` + `continue` antes do `_parse_message` quando `ignore_enabled and should_ignore(msg, codes)`.
- **Como foi feito / decisões:** o `_ignore_settings()` (F3) é resolvido **uma vez por `changes[].value`**, fora do laço — não vira leitura por mensagem. O warning segue o formato do §4 (`canal=… de=… wamid=… code=… title=… subtype=…`, os três últimos vindos de `describe_ignored`), com telefone e wamid, que é o rastro para investigar "eu te mandei!". Laço de `statuses[]` e ramo `else` do `_parse_message` intocados — o fallback `unsupported` continua existindo para quem desligar o toggle ou estreitar a lista.
- **Problemas / pendências:** um teste pré-existente do plano 75 (`test_todos_os_tipos_do_apendice_produzem_texto`) passava `UNSUPPORTED_MSG` pelo `parse_inbound` e ficou vermelho — mudança de comportamento esperada. Corrigido no lugar: o tipo saiu daquela lista (o formatter dele continua coberto por `test_unsupported_explica_o_motivo`, que chama `_parse_message` direto) e entrou um `test_unsupported_e_descartado_pelo_parse_inbound` afirmando o novo contrato. É o único arquivo fora do plugin que precisou de edição — e não é o `test_endpoints.py`.
- **Verificação:** `pytest tests/test_plano75_parse_inbound.py tests/test_plano75_cloud_inbound_text.py` verde; asserções diretas do gancho (payload real ⇒ `[]`; lote misto ⇒ só o texto; `statuses[]` preservado; toggle off ⇒ evento volta; log com telefone+wamid) em `tests/test_whatsapp_cloud_ignore_empty.py` (F4).

### F3 — Config no próprio plugin (🟢)

1. `[paralelo]` [settings.py](../assets/plugin_examples/whatsapp_cloud/settings.py): dois campos novos no `class Settings(BaseModel)` (**globais do plugin — D6**, valem para os dois canais Cloud) —
   - `ignore_empty_meta_messages: bool = True` — *"Ignorar mensagens que a Meta entrega sem conteúdo"*, com `description` explicando em PT-BR que a Meta às vezes entrega uma mensagem só com o aviso `Message type unknown`, sem texto nenhum (caso típico: código de verificação do Facebook para um número que está na API oficial), e que ligado **nada** é aberto por elas — sem atendimento, sem protocolo, sem IA, sem não-lida.
   - `ignore_error_codes: str = ""` — *"Códigos de erro ignorados"*, lista separada por vírgula. **Vazio (default, D5) = todas as mensagens sem conteúdo.** Preencher (ex.: `131051`) estreita a regra só àqueles códigos.
2. `[paralelo]` Helper de leitura em `channels.py`, com **cache TTL ~30s em variável de módulo** (não por instância — `parse_inbound` roda por webhook e não pode virar 1 SELECT por mensagem). Precedente de leitura no próprio plugin: [routes.py:72](../assets/plugin_examples/whatsapp_cloud/routes.py#L72) (`config_repo.get("plugin.whatsapp_cloud.graph_api_version")`).
3. `[sequencial]` Fail-open: falha ao ler config ⇒ usa `DEFAULT_IGNORED_ERROR_CODES` e o toggle ligado (mantém o comportamento pretendido) — **exceto** se a exceção vier do próprio `should_ignore`, onde a decisão é sempre "passa".

⚠️ Regra do CLAUDE.md: configuração de plugin mora **no plugin**. Nada disso pode ir para `ConfigPanel.js`.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída (2026-07-30)
- **O que foi feito:** `settings.py` ganhou `ignore_empty_meta_messages: bool = True` e `ignore_error_codes: str = ""` (com `title` + `description` em PT-BR no `Field`, texto de usuário: fala do código de verificação do Facebook e de que nada é aberto). `channels.py` ganhou `_IGNORE_CFG_TTL = 30.0`, o cache de módulo `_ignore_cfg_cache`, `_as_bool`, `_ignore_settings()` e `reset_ignore_settings_cache()`. Docstring do `settings.py` atualizada (não é mais "apenas a versão da Graph API").
- **Como foi feito / decisões:** `_ignore_settings()` importa `db.repositories.config_repo` **lá dentro** (import tardio), não no topo — assim o módulo continua carregável fora do app e o import defensivo do plugin não vira dependência dura do core. Cache em variável de MÓDULO (não por instância), TTL 30 s: o toggle passa a valer em ≤30 s, sem restart. `_as_bool` tolera `True`/`"true"`/`1`/`"0"`/`"false"` porque a chave pode ter sido gravada na mão (o `PluginSettingsForm` grava bool de verdade). Fail-open na LEITURA = manter o comportamento pretendido (ligado, lista default), enquanto o fail-open da REGRA continua sendo "passa" — as duas coisas são distintas de propósito, como pede o passo 3 da fase. `reset_ignore_settings_cache()` é o seam de teste (F5), inofensivo em produção.
- **Problemas / pendências:** nenhum. Nada tocado no `ConfigPanel.js` nem em qualquer arquivo do core.
- **Verificação:** `Settings().model_dump()` e `model_json_schema()` conferidos (defaults `True`/`""`, `title`/`description` presentes); toggle/lista exercitados via `monkeypatch` em `_ignore_settings` nos testes de `parse_inbound` (F4) e via config real no teste de integração (F5).

### F4 — Testes puros (🟢)

Arquivo novo `tests/test_whatsapp_cloud_ignore_empty.py`, carregando o módulo **por path** (molde de [test_plano75_cloud_inbound_text.py:20-34](../tests/test_plano75_cloud_inbound_text.py#L20-L34)) — sem DB, sem app.

| Teste | Fixture | Esperado |
|---|---|---|
| `test_ignores_real_meta_payload` | o item literal de §2.1, `codes=()` | `True` |
| `test_default_ignores_any_unsupported_code` | `type=unsupported`, `code=999999`, `codes=()` (**D5**) | `True` |
| `test_default_ignores_unsupported_without_errors` | `type=unsupported`, sem `errors`, `codes=()` | `True` |
| `test_keeps_text_message` | `{"type":"text","text":{"body":"oi"}}` | `False` |
| `test_keeps_named_unknown_type` | `{"type":"poll","poll":{…}}` — tipo nomeado, com payload | `False` (a âncora é o literal `"unsupported"`) |
| `test_narrowed_list_keeps_other_code` | `codes=(131051,)`, `code=999999` | `False` (estreitamento do operador funciona) |
| `test_narrowed_list_ignores_listed_code` | `codes=(131051,)`, `code=131051` | `True` |
| `test_malformed_errors_fails_open` | `errors="x"` / `errors=[{}]` **com** `codes=(131051,)` | `False` |
| `test_not_a_dict_fails_open` | `None`, `[]`, `"x"` | `False` |
| `test_parse_codes` | `"131051, 131052"`, `""`, `"lixo"`, `"131051,,x"` | `(131051,131052)`, `()`, `()`, `(131051,)` |
| `test_describe_ignored_has_code_and_title` | payload de §2.1 | string contém `131051` e `Message type unknown` |

#### Status de execução — Fase 4
**Estado:** ✅ Concluída (2026-07-30)
- **O que foi feito:** criado `tests/test_whatsapp_cloud_ignore_empty.py` — **38 testes**, sem DB e sem app. Cobre as 11 linhas pedidas na tabela da fase e mais 7: `test_default_codes_constant_is_empty` (a constante É o default D5), `test_narrowed_list_keeps_unsupported_without_errors`, `test_narrowed_list_matches_string_code` (a Meta alterna número × string), `test_describe_ignored_never_raises`, e o bloco do gancho de F2 (payload real ⇒ `[]`; lote misto ⇒ só o texto; `statuses[]` preservado; toggle off; lista estreitada; log com telefone+wamid) + `test_plugin_loads_without_the_new_module`.
- **Como foi feito / decisões:** `inbound_ignore.py` é carregado POR CAMINHO (molde do `test_plano75_cloud_inbound_text.py`); o `channels.py` sobe com o pacote sintético em `sys.modules` (molde do `test_plano75_parse_inbound.py`) para o `from .inbound_ignore import …` resolver. Nos testes do `parse_inbound` a config é injetada por `monkeypatch.setattr(_channels, "_ignore_settings", …)` — sem banco. O teste do import defensivo põe `sys.modules["<pkg>.inbound_ignore"] = None`, que faz o import relativo levantar `ImportError`, e então prova que `channels.py` ainda executa, `should_ignore` vira no-op e a mensagem volta a virar evento (é o item "remover o arquivo na mão ⇒ o plugin ainda carrega" do checklist, automatizado).
- **Problemas / pendências:** nenhum.
- **Verificação:** `WHATSBOT_TEST_DB_URL=…/whatsbot_test_95 venv/bin/python -m pytest tests/test_whatsapp_cloud_ignore_empty.py -q` ⇒ **38 passed**. Junto com os dois arquivos do plano 75: **200 passed**.

### F5 — Teste de integração (🔴)

Prova ponta a ponta de que **nada** é criado. POST do payload cru de §2.1 em `/api/webhook/whatsapp_cloud/{channel_id}` e, na sequência:

| Asserção | Alvo |
|---|---|
| `data.events == 0` | resposta do webhook ([channel_webhook.py:728](../server/routes/channel_webhook.py#L728)) |
| Nenhuma linha nova em `messages` | banco de teste |
| Nenhuma linha nova em `atendimentos` (nem reabertura de uma fechada) | banco de teste |
| Nenhum contato novo para um remetente inédito | banco de teste (o buraco do §2.3) |
| `unread` do contato inalterado | `unread_msg_ids` |
| Toggle **desligado** ⇒ tudo volta a acontecer | mesma rota, config off |
| Mensagem de texto no MESMO POST continua sendo ingerida | não pode virar descarte de lote inteiro |

⚠️ Suíte roda contra Postgres: exige `WHATSBOT_TEST_DB_URL` com `test` no nome do banco.

#### Status de execução — Fase 5
**Estado:** ✅ Concluída (2026-07-30)
- **O que foi feito:** criado `tests/test_plano95_ignore_empty_e2e.py` (**arquivo próprio — o `test_endpoints.py` não foi tocado**), 7 testes com POST no webhook real do canal Cloud: `events == 0`; remetente inédito **não vira contato**; conversa fechada **não reabre**, nada gravado em `messages` e `unread_count` intacto; lote misto (vazia + texto) mantém só o texto e conta 1 não-lida; `statuses[]` continua chegando; toggle off ⇒ tudo volta (linha `unsupported` salva); `ignore_error_codes="131051"` estreita (o 131051 some, o 999999 volta).
- **Como foi feito / decisões:** molde do `tests/test_plano82_system_inbound.py` (canal Cloud sintético + `build_app(["whatsapp_cloud"])`) e do plano 75 (`_drain` do orquestrador de batch, `message_batch_delay=0`, `auto_reply=False` — a IA nunca é exercida). O canal é criado numa fixture que roda **antes** do build: o app registra as instâncias vivas no startup e o webhook só parseia o que está no registry (a primeira versão criava o canal depois e o `parse_inbound` nem era chamado — foi o que apontou o erro). A config do descarte é isolada por uma fixture que salva/restaura as duas chaves (o banco é compartilhado na sessão) e chama `reset_ignore_settings_cache()` do módulo já carregado (`whatsbot_plugins.whatsapp_cloud.channels`) para o TTL de 30 s não mascarar a troca.
- **Problemas / pendências:** dois atritos de ambiente, ambos de teste e resolvidos: (1) o banco `whatsbot_test_95` não existia — criado com `ENCODING 'UTF8' TEMPLATE template0`; (2) `caplog` fica mudo depois que qualquer teste da sessão sobe o app, porque o `dictConfig` do boot desabilita os loggers pré-existentes (o módulo do plugin carregado por caminho é um deles) — o teste do warning passou a pendurar um handler no próprio logger e a reativá-lo (`disabled = False`), restaurando tudo no `finally`.
- **Verificação:** `WHATSBOT_TEST_DB_URL=…/whatsbot_test_95 venv/bin/python -m pytest tests/test_plano95_ignore_empty_e2e.py tests/test_whatsapp_cloud_ignore_empty.py tests/test_plano75_parse_inbound.py tests/test_plano75_cloud_inbound_text.py` ⇒ **207 passed** (ordem aleatória do `pytest-randomly` incluída). Regressão nos vizinhos que exercitam o canal Cloud (`test_plano75_failed_race`, `_bus_events`, `_error_card`, `_reply_e2e`, `test_plano82_system_inbound`, `test_multichannel_routing`, `test_whatsapp_cloud_template_prefs`, `test_channel_dedup_enforcement`, `test_channel_identity_hooks`) ⇒ **81 passed, 1 failed**.
- ⚠️ **A falha é PRÉ-EXISTENTE e alheia a este plano:** `test_multichannel_routing.py::test_guardrail_no_new_channel_blind_resolvers` acusa `server/routes/channel_webhook.py` — o `get_latest_for_contact` channel-blind da [linha 583](../server/routes/channel_webhook.py#L583), introduzido pelo commit do **plano 82** (`72b549d`) sem atualizar a allow-list do guard. O arquivo está **intocado** neste plano (`git diff HEAD` vazio) e é do core, fora do escopo D3 — fica registrado para quem for mexer no roteamento por canal.

### F6 — Release e limpeza (🔴)

1. `[sequencial]` `plugin.yaml` 1.8.0 → **1.9.0** + `description` explicando a mudança em linguagem de usuário.
2. `[sequencial]` Sincronizar `storages/plugins/whatsapp_cloud/` (dev) e **regravar** `assets/channel_plugins/whatsapp_cloud-plugin.zip`.
3. `[sequencial]` Produção: `Importar (.zip)` na tela Plugins. ⚠️ Conferir **antes** que produção ainda está em 1.8.0 e que a cópia lá não tem edição fora do git (o `telegram` já mostrou esse problema).
4. `[sequencial]` Limpeza do incidente: fechar o protocolo `#15353` (`PROT-20260730-140855-15353`) e o atendimento `855`.
5. `[sequencial]` Validar em produção: pedir um código 2FA à Meta e confirmar que **nada** aparece no painel e que o `logger.warning` saiu.

#### Status de execução — Fase 6
**Estado:** 🟡 Parcial — F6.1 e F6.2 feitas; **F6.3, F6.4 e F6.5 dependem de ação manual em produção** (2026-07-30)
- **O que foi feito:** (1) `plugin.yaml` **1.8.0 → 1.9.0**, com um parágrafo novo de `description` em linguagem de usuário (fala do código de verificação do Facebook, do que deixa de acontecer e de onde desligar/estreitar); (2) `storages/plugins/whatsapp_cloud/` sincronizado (`channels.py`, `settings.py`, `inbound_ignore.py`, `plugin.yaml` — a divergência de `description` do §2.5 deixou de existir; sobra só a pasta `tests/` vazia, que é local) e `assets/channel_plugins/whatsapp_cloud-plugin.zip` **regravado** (16 arquivos, mesmo layout de antes — arquivos na raiz, sem entradas de diretório, sem `__pycache__`/`.pyc`/`.db` — agora com `inbound_ignore.py` dentro, conferido por `zipfile`).
- **Como foi feito / decisões:** o zip é gerado de `assets/plugin_examples/whatsapp_cloud/` (a fonte no git), nunca da cópia instalada. Verificação read-only de produção antes do deploy, como o passo 3 exige: `plugins.version` do `whatsapp_cloud` lá é **1.8.0** e `load_error` é nulo — sem o drift que o `telegram` tem (prod roda 1.2.2, que não existe no git). O incidente também foi confirmado no banco de produção: `atendimentos.id = 855` está **`open`** (inbox 21, contato 1210, sem responsável) e `plugin_protocolos_protocolos.id = 15353` está **`aberto`** (telefone 447974905044, sem responsável).
- **Problemas / pendências (3, todas suas):**
  1. **F6.3 — importar em produção:** subir `assets/channel_plugins/whatsapp_cloud-plugin.zip` em Plugins → `Importar (.zip)`. Sem isso o plano não tem efeito nenhum em produção (o card tem que passar a mostrar **1.9.0**).
  2. **F6.4 — limpeza:** fechar o protocolo `#15353` e o atendimento `855` **pela UI**. Não fiz por SQL de propósito: escrever direto no banco de produção pularia os hooks do plugin (avisos no fio, auditoria, ciclo do protocolo) e deixaria estado inconsistente.
  3. **F6.5 — validação em produção:** pedir um código 2FA à Meta e confirmar painel silencioso + o `WARNING` `[whatsapp_cloud] mensagem sem conteúdo descartada …` no log.
- **Verificação:** `plugin.yaml` reparseado (`version: 1.9.0`); zip validado por `zipfile` + `yaml.safe_load` (id `whatsapp_cloud`, versão 1.9.0, `inbound_ignore.py` presente); `diff -rq assets/… storages/…` limpo; estado de produção consultado read-only (versão 1.8.0, protocolo/atendimento ainda abertos).

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Cliente real manda **enquete / view-once** | Chega como `unsupported` e, com **D5** (descartar tudo), some **sem bolha e sem badge** — o operador não fica sabendo. **É o risco central deste plano, aceito pelo usuário em 2026-07-30** | Nota: a mensagem já era ilegível antes (bolha de aviso sem conteúdo). Mitigações: `logger.warning` com **telefone + wamid** em todo descarte (é o rastro para investigar uma reclamação "eu te mandei!"); payload cru continua em `/api/channel-webhook-payloads` e no `debug_bus`; toggle desliga tudo e `ignore_error_codes="131051"` estreita para só o caso da Meta — ambos sem redeploy |
| Config lida por mensagem | 1 SELECT por webhook | Cache TTL ~30s em variável de módulo (F3.2) |
| Exceção na regra | Engolir mensagem de cliente | **Fail-open** em todos os níveis (§4); coberto por `test_malformed_errors_fails_open` |
| Zip antigo sem `inbound_ignore.py` | `ImportError` derruba o carregamento do plugin ⇒ canal fora do ar | Import defensivo idêntico ao do `describe_message` ([channels.py:224-228](../assets/plugin_examples/whatsapp_cloud/channels.py#L224-L228)) |
| Descarte de lote | Um `messages[]` com uma vazia + uma de texto perder as duas | O `continue` é **por item**; travado por asserção em F5 |
| Recibos (`statuses[]`) | Perder `sent`/`delivered`/`failed` | Laço separado, não tocado (§3.1) |
| Sumiço do rastro | "A Meta tentou e ninguém soube" | `filter.webhook.payload` + `/api/channel-webhook-payloads` + o warning (§2.4) |
| 4 cópias do plugin | Editar a cópia errada / número de versão igual com conteúdo diferente | Editar **`assets/`**, regravar o zip, comparar **conteúdo** e não só versão (§2.5) |
| Deploy | Produção só muda por `Importar (.zip)` manual | F6.3 explícito; sem isso o plano não tem efeito nenhum em produção |
| Segundo canal Cloud | `whatsapp_oficial_disparo` (inbox 18) herda a regra | É desejável (mesma plataforma, mesmo defeito) — mas registrar em **P2** |

---

## 7. Perguntas em aberto

**P1 — Descartar por completo, ou manter a bolha no fio sem acionar nada?**
Contexto: descarte total some com a evidência visual; a alternativa ("card cinza painel-only, sem reabrir/sem badge/sem IA/sem protocolo") exigiria o conceito de *inbound não-acionável* no core — provider declara, core avalia, no molde de `MediaLimits`/`TemplateSpec`.
(a) Descartar no plugin — cabe em D3, resolve hoje.
(b) Card painel-only — melhor UX, **mas viola D3** e vira plano próprio.
**Recomendação: (a)** agora; (b) fica registrado como evolução se o item de risco "cliente real" se materializar. ✅ **DECIDIDO (2026-07-30)** pela restrição D3.

**P2 — A regra vale para os dois canais Cloud (`Atendimento` inbox 21 e `whatsapp_oficial_disparo` inbox 18)?**
(a) Setting global do plugin — uma chave, vale para todo canal Cloud.
(b) Campo por canal no `provider_descriptor` (`config_fields`), lido por `_cred`.
✅ **DECIDIDO (2026-07-30): (a) global do plugin** — o defeito é da plataforma, não do número. Ver **D6**.

**P3 — Default da lista de códigos: só `131051`, ou qualquer `unsupported`?**
(a) `"131051"` — conservador; um `unsupported` novo continua aparecendo.
(b) Vazio (= qualquer `unsupported`) — mata todo ruído de uma vez.
✅ **DECIDIDO (2026-07-30): (b) tudo.** Ver **D5**. Consequência explicitada e aceita: enquete/view-once de cliente real também é descartada em silêncio — o `logger.warning` com telefone + wamid é o rastro, e `ignore_error_codes="131051"` reverte para o conservador sem redeploy.

**P4 — Melhorar o texto do fallback (`describe_unsupported`)?**
✅ **DECIDIDO (2026-07-30): (a) deixar como está.** Ver **D7**. Com D5 esse texto praticamente não renderiza mais para `unsupported`, então reescrevê-lo seria código sem efeito prático. `inbound_text.py` **não é tocado** neste plano.

**P5 — Limpeza retroativa: o que fazer com o que já ficou para trás?**
Duas coisas diferentes: (i) a **sobra do incidente** — protocolo `#15353` e atendimento `855`, ambos abertos e sem responsável agora; (ii) as **4 linhas `unsupported` já salvas** no banco (2 deste caso + 3 de outros remetentes, 2026-07-15 a 2026-07-29). O plano impede novas, mas não apaga nenhuma das duas.
✅ **DECIDIDO (2026-07-30): fechar (i) na UI (F6.4); NÃO mexer em (ii).** Ver **D8** — apagar mensagem já salva mexe em histórico de conversa de cliente por ganho nulo; são 4 bolhas inertes.

---

## 8. Checklist de verificação

- [x] `should_ignore` é puro (stdlib-only) e não importa nada de `db/`, `app/`, `server/`
- [x] Fail-open provado: payload malformado, `None`, `errors` estranho ⇒ mensagem **passa** (`test_malformed_errors_fails_open`, `test_not_a_dict_fails_open`)
- [x] Import defensivo: remover `inbound_ignore.py` na mão ⇒ o plugin ainda **carrega** (automatizado em `test_plugin_loads_without_the_new_module`)
- [x] `parse_inbound` com o payload real ⇒ `[]`; com payload misto (vazia + texto) ⇒ só a de texto
- [x] Default = **todo** `unsupported` descartado, qualquer código (D5) — inclusive sem `errors`
- [x] Tipo **nomeado** desconhecido (ex.: `"poll"` com payload) **continua passando** — a âncora é o literal `"unsupported"`
- [x] `ignore_error_codes = "131051"` estreita a regra (um `unsupported` com outro código volta a aparecer) — puro **e** ponta a ponta
- [x] Todo descarte loga `WARNING` com **telefone + wamid** (`test_discard_logs_phone_and_wamid`)
- [x] `tests/test_whatsapp_cloud_ignore_empty.py` verde (38 testes)
- [x] `tests/test_plano75_cloud_inbound_text.py` continua verde (o fallback `unsupported` não regrediu)
- [x] Suíte verde no Postgres de teste (`WHATSBOT_TEST_DB_URL` → `whatsbot_test_95`) — 207 passed nos 4 arquivos do escopo
- [x] Integração: zero linha nova em `contacts`/`messages`/`atendimentos` e `unread` intacto
- [x] Toggle off ⇒ comportamento antigo volta, sem restart (TTL de 30 s)
- [x] Recibos (`statuses[]`) do mesmo remetente continuam chegando
- [ ] Toggle e lista aparecem legíveis em **Configurar → WhatsApp Cloud API**, no **modo escuro** também — *o form é o `PluginSettingsForm` genérico do core (bool + string), já usado pelo `graph_api_version`; conferência visual pendente*
- [x] Nenhum arquivo fora de `assets/plugin_examples/whatsapp_cloud/`, `assets/channel_plugins/`, `storages/plugins/whatsapp_cloud/` (a cópia dev, F6.2) e `tests/` foi tocado (D3)
- [x] `plugin.yaml` em 1.9.0 e zip regravado — [ ] **produção reimportada e mostrando 1.9.0 (pendente, F6.3)**
- [ ] Validação em produção: pedir um código 2FA e confirmar painel silencioso + warning no log (**pendente, F6.5**)
- [ ] Protocolo `#15353` e atendimento `855` fechados (**pendente, F6.4** — ambos confirmados ainda abertos no banco de produção)

---

## 9. Apêndice — arquivos-chave

**Plugin (único lugar que muda — D3):**
- `assets/plugin_examples/whatsapp_cloud/inbound_ignore.py` — **novo**, a regra
- `assets/plugin_examples/whatsapp_cloud/channels.py` — import defensivo (~224) + `continue` no laço de `messages[]` (~1043)
- `assets/plugin_examples/whatsapp_cloud/settings.py` — toggle + lista de códigos
- `assets/plugin_examples/whatsapp_cloud/plugin.yaml` — bump 1.8.0 → 1.9.0
- `assets/channel_plugins/whatsapp_cloud-plugin.zip` — regravar
- ~~`assets/plugin_examples/whatsapp_cloud/inbound_text.py:288`~~ — **NÃO tocar** (D7/P4)

**Core (somente leitura — referência, NÃO muda):**
- `server/routes/channel_webhook.py:716-731` — chamada do `parse_inbound` e resposta
- `app/services/message_ingest_service.py:410,429,455,471,482` — contato, unread, `before_save`, reopen, atendimento
- `app/services/messaging_service.py:891-925` — batch + `message.saved`
- `storages/plugins/protocolos/events.py:27` — por que o protocolo não é mais chamado
- `storages/plugins/protocolos/logic.py:3174` — a regra por regex que este plano **não** usa

**Testes:**
- `tests/test_whatsapp_cloud_ignore_empty.py` — **novo** (puro)
- `tests/test_plano75_cloud_inbound_text.py:20-34` — molde de carregamento por path
- `tests/test_endpoints.py` — teste de integração do webhook (F5)

**Evidência do caso:**
- `debug_bus_1785431496831.jsonl:3703-3707` — payload cru, reabertura, `message.received`
- Produção: contato `1210`, atendimento `855` (inbox 21), protocolo `15353`, `usage` ids `2097`/`2113`
