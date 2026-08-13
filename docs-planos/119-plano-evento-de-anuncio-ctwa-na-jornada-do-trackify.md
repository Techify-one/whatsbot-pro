# Plano 119 — O clique de anúncio (CTWA) vira EVENTO no Trackify e NOTA PRIVADA no chat

> **Status:** ✅ **IMPLEMENTADO** (2026-08-13) — plugin `janela_72h` **1.5.0** instalado localmente,
> **não publicado**. Fases 0-8 concluídas; suíte do plugin 85/85, suíte do core nas 3 falhas
> pré-existentes. Pendências: teste de ponta a ponta com clique real + as decisões **P6** (badge de
> não-lida) e **P7** (`ai_history_exclude_patterns`), que são do usuário.
> **Data:** 2026-08-13 · **Escopo:** pequeno/médio
> **Origem:** pedido do usuário — *"No plugin de 72horas, quero que envie para o plugin do trackify para
> mandar um evento no histórico do lead para quando ele vier de anúncio da meta. Atualmente o plugin é
> responsável por colocar uma tag de 72 horas e também colocar em um campo de protocolo qual foi a
> campanha de origem e quero que agora envie para o plugin do trackify para ele enviar para o trackify
> de verdade o evento para o cliente."* (com dois prints: a "Jornada do cliente" do lead **Aluhan
> Scripts z** mostrando **2 eventos** — `protocolo_opened` e `conversation_reopened` — e o campo
> **Campanha** do protocolo já preenchido com `[C037][COMPRA][QUENTE+ADV][11-05-2026][COMBO-SEGURANÇA`).
> **Método:** leitura do código real (core + os dois plugins) + consulta ao **banco de produção**
> (`whatsbot` @ `banco-privado-redes-brasil-geral`, leitura). Todo `arquivo:linha` abaixo foi verificado
> nesta sessão; os números de produção são medidos, não estimados.
>
> **São DOIS entregáveis independentes**, e essa independência é a decisão estrutural do plano:
> **(A)** o clique vira **evento na jornada do Trackify** (via `plugins.services` → `track_event`);
> **(B)** o clique vira **nota privada no fio da conversa**, para o atendente ver a campanha **sem
> precisar abrir o modal "Resolver"** — hoje o valor só existe no rótulo do protocolo, que é
> justamente onde o print do usuário o mostra: dentro do formulário de resolução.
>
> O trabalho é **inteiramente de plugin** — zero mudança no core. O `janela_72h` já tem o dado (captura
> o `referral` e resolve a campanha na Graph); o `trackify` já tem o portão de entrada pronto e
> **desenhado exatamente para este caso** (a op `track_event`, cuja docstring diz literalmente *"Existe
> para OUTROS plugins"*); e a nota privada tem precedente literal no `protocolos`, que já escreve
> `🔖 Protocolo aberto · PROT-…` no mesmo fio.
>
> ⚠️ **Pré-requisito que vale só para (A)** (§2.3): o seam `plugins.services` **não está no checkout
> local** — ele vive em `origin/developer` e o `developer` local está **5 commits atrás**. Produção já
> roda com ele (provado por dado, §2.4). A Fase 0 é sincronizar o checkout; sem isso a costura do
> Trackify é escrita mas nunca executa. **(B) não depende disso** e pode ser entregue mesmo com a F0
> bloqueada.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar
> para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| D1 | ✅ (2026-08-13) O `janela_72h` **entrega ao `trackify`**, e é o `trackify` quem fala com o CDP | O `janela_72h` nunca chama a API do Trackify por HTTP nem monta payload de ingestão. Ele chama `services.call("trackify", "track_event", …)` e para por aí. Toda a tradução para o vocabulário do CDP (`external_id`, `install_id`, bloco `identity`, fila de saída, retry) fica **100% dentro do trackify** — mesmo contrato que o `protocolos` já usa ([logic.py:5313-5316](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py)) |
| D2 | ✅ (2026-08-13) O comportamento atual **não muda**: a etiqueta de 72h e o rótulo de campanha no `protocolos` continuam exatamente como estão | O evento é **aditivo**. Nenhuma linha de `record_and_label`, `sweep_once` ou `protocolos_bridge` é reescrita. Se o trackify estiver ausente/desligado, o plugin funciona como hoje |
| D3 | ✅ Regra do repo — **tudo que pode ficar no plugin fica no plugin** (CLAUDE.md §"O que fica no core e o que vai pro plugin") | Zero mudança em `agent/`, `server/`, `db/` ou `channels/`. O único toque no core é **sincronizar** o checkout com `origin/developer` (código já escrito e já em produção), não escrever core novo |
| D4 | ✅ Regra do repo — **nasce desligado** | O envio ao Trackify é uma chave de configuração nova com default `False`, como o `campaign_field_key` já faz ([store.py:84-88](../storages/plugins/janela_72h/store.py)). Quem atualizar o plugin não ganha comportamento novo sem pedir |
| D5 | ✅ Regra do repo — **import defensivo** | `from plugins import services` entra em `try/except` no topo do módulo. Um `raise` ali significa **plugin que não carrega**, falha muda no boot (precedente literal: [protocolos/logic.py:51-54](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py)) |
| D6 | ✅ (2026-08-13) A campanha também vira **nota privada no fio da conversa** | *"pode ter também é injetar uma mensagem privada caso encontre qual campanha da meta é para não precisar clicar no botão de resolver atendimento para descobrir"*. Entregável **(B)**, com interruptor próprio e **independente do Trackify** (§4.5) — quem só quer a nota não precisa do CDP, e quem só quer o evento não ganha nota no chat |

---

## 1. Resumo executivo

Quando um lead clica num anúncio Click-to-WhatsApp, o `janela_72h` já faz **duas** coisas: marca a
conversa com a etiqueta `janela_72_horas` e grava o nome da campanha no rótulo `campanha` do protocolo.
Ele tem, portanto, o dado mais valioso do funil — **de qual anúncio e de qual campanha aquele lead
veio** — e esse dado morre dentro do WhatsBot. No CDP, o mesmo lead aparece como um contato que
simplesmente existe: o print do usuário mostra a jornada do **Aluhan Scripts z** com dois eventos
(`Protocolo aberto`, `Atendimento reaberto`) e **nenhuma menção ao anúncio** que o trouxe.

O conserto é uma costura, não uma máquina nova. O `trackify` 4.0.0 publica uma API interna in-process
(`entry.services`) cuja op `track_event` foi escrita **para este exato uso** — enfileirar um evento
genérico na timeline de um contato a pedido de outro plugin ([trackify/services.py:96-124](../../whatsbot-pro-plugins/plugins/trackify/src/services.py)).
O `janela_72h` passa a chamá-la com um `kind` próprio (`anuncio_clicado`), o `occurred_at` do clique e
os dados do anúncio já persistidos na tabela dele.

O segundo entregável (D6) resolve um problema de **visibilidade dentro do próprio painel**, e é
menor: hoje a campanha só existe no rótulo `campanha` do protocolo, e o único lugar onde o atendente a
enxerga é o formulário de **Resolver** — exatamente o que o segundo print do usuário mostra. Uma nota
privada `📣 Lead de anúncio · Campanha: …` no fio da conversa põe a informação onde ela é útil: **antes**
do atendimento, não no fim. O mecanismo já existe e tem precedente literal no `protocolos`
([logic.py:1110-1139](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py)), então isto é
reuso de padrão, não invenção.

Três restrições de desenho saem da leitura do código e **não são negociáveis**:

1. **A emissão não pode entrar no laço `apply_campaigns_once`.** Aquele laço tem uma única coluna de
   consumo (`consumed_at`) e desiste cedo em três situações (`campaign_field_key` vazio, `protocolos`
   indisponível, `sem_protocolo`) — §4.2. Pendurar o Trackify nele faria o evento depender do
   `protocolos` estar instalado e configurado, o que a D2 proíbe. Solução: **coluna de dedupe própria**
   + um quarto passo independente na varredura.
2. **O `external_key` tem de ser o `wamid`.** A fila do trackify deduplica por `external_id` com
   `ON CONFLICT DO NOTHING` ([mirror.py:203-215](../../whatsbot-pro-plugins/plugins/trackify/src/mirror.py)),
   então uma chave natural estável transforma reentrega da Meta, re-varredura e restart em no-op — a
   rede de segurança que torna seguro emitir de dois lugares (§4.1).
3. **A nota privada precisa de `reopen=False` e de um padrão em `ai_history_exclude_patterns`.** Ela é
   escrita por automação, não por humano: sem o primeiro, uma nota atrasada **reabre** uma conversa já
   resolvida; sem o segundo, ela **entra no contexto do LLM** — e em produção esse filtro está hoje
   vazio (`[]`), então a nota do `protocolos` já entra (§4.5).

---

## 2. Como funciona hoje (mapa verificado)

### 2.1 `janela_72h` — os dois sinais e os três passos da varredura

O plugin observa o webhook cru da Meta em `filter.webhook.payload` com prioridade 9000 e **sempre
devolve o payload intacto** ([filters.py:76-112](../storages/plugins/janela_72h/filters.py)) — devolver
`None` ali descartaria a mensagem do cliente.

⚠️ **São dois eventos diferentes do mesmo webhook** e o próprio código avisa para não confundi-los
([ad_referral.py:7-20](../storages/plugins/janela_72h/ad_referral.py)):

| Sinal | Onde aparece no payload | Quem lê | O que produz |
|---|---|---|---|
| Janela de 72h | `statuses[].conversation.origin.type` | `store.detect_72h` ([store.py:213](../storages/plugins/janela_72h/store.py)) | etiqueta de conversa `janela_72_horas` |
| **Anúncio (CTWA)** | `messages[].referral` (mensagem **inbound**) | `ad_referral.detect_referrals` ([ad_referral.py:34-67](../storages/plugins/janela_72h/ad_referral.py)) | linha em `plugin_janela_72h_ad_leads` |

O referral chega **antes** do status (medido em produção pelo autor do plano 113: de 25 s a 4,5 h). Os
campos persistidos são `source_id`, `source_type`, `source_url`, `ctwa_clid`, `headline`
([ad_referral.py:27](../storages/plugins/janela_72h/ad_referral.py)) mais `phone`, `msg_id` (wamid),
`channel_id` e `ts`.

A varredura ([lifecycle.py:42-68](../storages/plugins/janela_72h/lifecycle.py)) roda **três trabalhos
independentes**, cada um em `try/except` próprio — "uma falha da Graph não pode impedir a expiração de
uma etiqueta":

| Passo | Função | `arquivo:linha` | O que faz |
|---|---|---|---|
| 1 | `sweep_once` | [store.py:422](../storages/plugins/janela_72h/store.py) | remove a etiqueta das janelas expiradas |
| 2 | `resolve_ads_once` | [store.py:604](../storages/plugins/janela_72h/store.py) | anúncio → campanha na Graph, cacheado em `plugin_janela_72h_ad_cache` |
| 3 | `apply_campaigns_once` | [store.py:700-767](../storages/plugins/janela_72h/store.py) | grava o nome da campanha no rótulo do `protocolos` |

Há ainda um caminho **rápido**: depois de capturar um clique, `_capture_and_follow` insiste com os
degraus `(6, 12, 30, 60, 90)` segundos chamando `nudge_pending`
([filters.py:34](../storages/plugins/janela_72h/filters.py), [filters.py:53-73](../storages/plugins/janela_72h/filters.py),
[store.py:772-790](../storages/plugins/janela_72h/store.py)) — porque o protocolo nasce ~5 s depois do
inbound e a varredura de 300 s daria a impressão de que a feature não funcionou. **Isso é medido em
produção**: o lead do print foi capturado às 16:36:21 e consumido às **16:36:27** — 6 segundos.

⚠️ **Contexto de thread** (importa para a §4.4): tudo o que toca banco/rede roda em
`asyncio.to_thread` — a varredura em [lifecycle.py:44-57](../storages/plugins/janela_72h/lifecycle.py)
e o caminho rápido em [filters.py:61](../storages/plugins/janela_72h/filters.py) e
[filters.py:68](../storages/plugins/janela_72h/filters.py). Nenhum deles roda na thread do event loop.

### 2.2 O portão do `trackify` — `track_event`

`trackify/services.py` é o **único** ponto de entrada de outro plugin no CDP, e é declaradamente
**folha**: nenhum outro módulo do trackify o importa, para que o plugin continue carregando num core
que não conhece `entry.services` ([services.py:8-11](../../whatsbot-pro-plugins/plugins/trackify/src/services.py)).

```python
def track_event(kind, *, contact_id=None, phone="", data=None,
                occurred_at=None, external_key="", title="") -> dict
```
([services.py:96-124](../../whatsbot-pro-plugins/plugins/trackify/src/services.py))

| Guard | `arquivo:linha` | Efeito |
|---|---|---|
| `_need_mirror()` | [services.py:71-74](../../whatsbot-pro-plugins/plugins/trackify/src/services.py) | `mirror_enabled=False` ⇒ `ServiceDisabled` ⇒ envelope `DISABLED` |
| `_KIND_RE` | [services.py:91](../../whatsbot-pro-plugins/plugins/trackify/src/services.py) | `^[a-z][a-z0-9_]{0,63}$` |
| `RESERVED_KINDS` | [services.py:92-93](../../whatsbot-pro-plugins/plugins/trackify/src/services.py) | os 11 `KIND_*` internos ([mirror.py:33-46](../../whatsbot-pro-plugins/plugins/trackify/src/mirror.py)) são proibidos — reusar um colidiria no dedup do CDP |
| `mirror.eligible(contact)` | [mirror.py:145-166](../../whatsbot-pro-plugins/plugins/trackify/src/mirror.py) | recusa grupo e `contact_type` fora de `mirror_contact_types` |
| `external_id` | [services.py:120-123](../../whatsbot-pro-plugins/plugins/trackify/src/services.py) + [mirror.py:134-135](../../whatsbot-pro-plugins/plugins/trackify/src/mirror.py) | `wb.<install_id>.ext.<kind>.<external_key>`; `external_key` vazio cai em `f"{contact_id}.{int(ts)}"` — **instável**, evitar |
| `title` | [mirror.py:177-181](../../whatsbot-pro-plugins/plugins/trackify/src/mirror.py) | *"um `kind` vindo de fora PRECISA passar o seu, senão renderiza como slug cru"* |
| dedupe | [mirror.py:203-215](../../whatsbot-pro-plugins/plugins/trackify/src/mirror.py) | `INSERT … ON CONFLICT (external_id) DO NOTHING` |

O `data` é limpo de vazios antes de virar payload
([mirror.py:200](../../whatsbot-pro-plugins/plugins/trackify/src/mirror.py)) e `external_id` é `TEXT`
sem limite ([001_initial.sql:21](../../whatsbot-pro-plugins/plugins/trackify/src/migrations/001_initial.sql)),
então um wamid de ~70 caracteres cabe.

### 2.3 O seam `plugins.services` no core — e o pré-requisito

| Item | Onde | Situação |
|---|---|---|
| `plugins/services.py` (423 linhas) | `origin/developer` | ✅ existe · ❌ **não está no checkout local** |
| `_entry_services` | `origin/developer:plugins/loader.py:283-292` | registra `SERVICES` sem nunca tocar `loaded.router` (invisível a HTTP) |
| `"services"` em `_ENTRY_SPECS` | `origin/developer:plugins/loader.py:337` | 9ª chave de `entry` |
| `register_plugin_services` / `register_plugin_uses` | `origin/developer:server/app.py:177-184` | chamados no boot, por plugin carregado |
| `uses_services` no manifest | `origin/developer:plugins/manifest.py:82` e `:221` | bloco do **consumidor** |
| `WHATSBOT_API_VERSION` | `origin/developer:plugins/semver.py:45` = **1.2.0** · local `plugins/semver.py:39` = **1.1.0** | o seam subiu como MINOR |

A API de chamada é um envelope que **nunca levanta**
(`origin/developer:plugins/services.py:407-411`):

```python
services.call("trackify", "track_event", _as="janela_72h", **kwargs) -> ServiceResult
```

Vereditos relevantes: `OK` · `UNAVAILABLE` (plugin não carregado) · `UNKNOWN_OP` · `INCOMPATIBLE` ·
`DISABLED` (espelho desligado) · `WRONG_CONTEXT` (op async chamada de forma síncrona **na thread do
loop**) · `ERROR` (`origin/developer:plugins/services.py:53-61`). `track_event` é **síncrona**, então o
ramo `WRONG_CONTEXT` não se aplica a ela — mas a chamada continua devendo sair de worker thread por
disciplina.

⚠️ **O `developer` local está 5 commits atrás de `origin/developer`** (`d0d9976`, `949525d`, `329bcbd`,
`dc77a11`, `f087fe3`). No checkout de hoje `plugins/services.py` não existe, `_ENTRY_SPECS` tem 8
chaves e o `entry.services` do trackify é **silenciosamente ignorado**. Isto já estava documentado no
plano 114 §2.6.

### 2.4 O que produção mostra hoje (medido, 2026-08-13)

| Fato | Valor |
|---|---|
| `trackify` | **4.0.0**, `enabled=1`, sem `load_error` |
| `janela_72h` | **1.4.0**, `enabled=1` |
| `protocolos` | **1.33.0**, `enabled=1` |
| `plugin.trackify.mirror_enabled` | **`true`** ✅ (o guard `_need_mirror` passa) |
| `plugin.trackify.mirror_contact_types` | `"whatsapp"` (o lead de CTWA é `whatsapp` ⇒ elegível) |
| `plugin.janela_72h.campaign_field_key` | `"campanha"` · scope `protocolo` |
| `plugin_trackify_outbox` | 10 kinds, **1 149 linhas, todas `sent`**; `protocolo_opened` mais recente **hoje 16:42:14** |
| `plugin_janela_72h_ad_leads` | **2 linhas**, ambas `source_type='ad'`, ambas já consumidas, campanha resolvida sem erro |

O `protocolo_opened` entregue hoje às 16:42 é a **prova executável de que o seam `plugins.services`
está vivo em produção**: quem o entrega é `protocolos.logic._emit_bus` via `services.call`, e sem o
seam o `_services` seria `None` e nada teria saído.

Os 2 leads são exatamente os dois cliques de anúncio já capturados — um deles é o contato do print:

| phone | campanha resolvida | capturado → consumido |
|---|---|---|
| `559282902622` (**Aluhan Scripts z**) | `[C037][COMPRA][QUENTE+ADV][11-05-2026][COMBO-SEGURANÇA` | 16:36:21 → 16:36:27 (**6 s**) |
| `556596106660` | `[C038][VENDA][PERPETUO][ADV+][28-04-26]-COMBO-MONITORAMENTO` | 01:04:46 → 01:04:53 (7 s) |

E a jornada desse mesmo contato no CDP tem **2 eventos**, nenhum deles o anúncio. **É literalmente o
gap do pedido, com nome e sobrenome.**

---

## 3. Inventário / análise

### 3.1 O que falta

| # | Item | Onde | O que falta | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|---|
| I1 | **(A)** Checkout do core sincronizado | `plugins/services.py` (ausente) | o seam inteiro | `git merge origin/developer` (código já em produção) | médio | S |
| I2 | **(A)** Declaração de consumo | [janela_72h/plugin.yaml](../storages/plugins/janela_72h/plugin.yaml) | bloco `uses_services` | copiar o do `protocolos` ([plugin.yaml:204-206](../../whatsbot-pro-plugins/plugins/protocolos/src/plugin.yaml)) | baixo | S |
| I3 | **(A)** Ponte plugin→plugin | novo `trackify_bridge.py` | não existe | irmão do [protocolos_bridge.py](../storages/plugins/janela_72h/protocolos_bridge.py), mesmas regras (import defensivo, só aditivo) | baixo | M |
| I4 | Dedupe própria (×2) | `plugin_janela_72h_ad_leads` | colunas `trackify_at` **e** `note_at` | uma migration `003_*.sql` para as duas (§4.2) | baixo | S |
| I5 | **(A)** Passo 4 da varredura | [lifecycle.py:42-68](../storages/plugins/janela_72h/lifecycle.py) + [store.py:772-790](../storages/plugins/janela_72h/store.py) | `emit_ads_to_trackify_once()` | 4º `try/except` independente + entrada no `nudge_pending` | médio | M |
| I6 | **(B)** Nota privada da campanha | novo `note.py` (ou função em `store.py`) + [lifecycle.py:58-63](../storages/plugins/janela_72h/lifecycle.py) | não existe | `agent_handler._get_contact` → `add_message("private_note", …, reopen=False)` → `broadcast("new_message")`, exatamente como [protocolos/logic.py:1110-1139](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py) | médio | M |
| I7 | Configuração | [store.py:78-93](../storages/plugins/janela_72h/store.py), [routes.py](../storages/plugins/janela_72h/routes.py), [static/janela_72h.js](../storages/plugins/janela_72h/static/janela_72h.js) | 2 chaves + 2 toggles + status | espelhar o padrão do `campaign_field_key` | baixo | M |
| I8 | Testes | [janela_72h/tests/python/](../../whatsbot-pro-plugins/plugins/janela_72h/tests/python/) | nenhum cobre ponte nem nota | fake provider no registry real + asserção sobre a row `private_note` (F6) | baixo | M |
| I9 | Versão + ZIP + instalação | `plugin.yaml`, `janela_72h.zip` | 1.4.0 → 1.5.0 | `build_plugins.py` + **instalar local antes de publicar** | baixo | S |

### 3.2 Falsos positivos descartados

| Suspeita | Por que **não** é problema |
|---|---|
| "Precisa de um evento novo no bus do core (`KNOWN_EVENTS`)" | Não. `plugins.services` é request/response e não passa pelo bus. Nenhum catálogo do core muda ⇒ **nenhum bump de `WHATSBOT_API_VERSION`** por causa deste plano |
| "Precisa expor um endpoint no `trackify` para o `janela_72h` chamar" | Proibido por desenho: `services.py` é *"in-process, NUNCA HTTP"* e o loader garante isso não tocando em `loaded.router` (`origin/developer:plugins/loader.py:285-287`) |
| "O `janela_72h` precisa saber vincular o contato no CDP" | Não. `track_event` só enfileira; identidade, retry e cadastro são do dispatcher do trackify. O `janela_72h` manda `contact_id`/`phone` e para |
| "Emitir de dois lugares (varredura + caminho rápido) vai duplicar evento" | Não, **desde que o `external_key` seja estável**: `ON CONFLICT (external_id) DO NOTHING` ([mirror.py:213](../../whatsbot-pro-plugins/plugins/trackify/src/mirror.py)) absorve. É por isso que a §4.1 fixa o wamid |
| "Dá para reusar o `kind` `conversation_created`" | Proibido: está em `RESERVED_KINDS` ([services.py:92-93](../../whatsbot-pro-plugins/plugins/trackify/src/services.py)) e colidiria no dedup do CDP com o evento que o próprio trackify produz |
| "O `mirror_enabled` pode estar desligado em produção e o plano morre" | Verificado: **`true`** (§2.4) |
| "A cópia instalada em `storages/plugins/janela_72h` divergiu da fonte" | Verificado com `diff -rq`: **byte-idêntica** ao `src/` do repo de plugins. (A armadilha é real — ver o histórico do `protocolos` — mas hoje não se aplica) |
| "`plugin.janela_72h.campaign_field_key` é segredo (veio mascarado na consulta)" | Falso positivo **da minha própria consulta**: o `CASE … ILIKE '%key%'` mascarou pelo nome da chave. O valor é `"campanha"`, o slug de um rótulo. Os segredos de verdade são só `meta_ads_token` e `meta_app_secret` ([store.py:97](../storages/plugins/janela_72h/store.py)) |
| **(B)** "A nota privada precisa de rota/endpoint novo, ou de mexer no core" | Não. `ContactMemory.add_message` é a API que o `protocolos` já usa de dentro de um plugin ([logic.py:1131](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py)); o `agent_handler` vem de `plugins.context.get_deps()`. Zero core |
| **(B)** "A nota privada vai virar mensagem para o cliente" | Não. `private_note` é **painel-only** — não vai ao WhatsApp. Confirmado no core: é role excluída do preview em [_mapping.py:105](../db/repositories/_mapping.py) e tratada como card no painel |
| **(B)** "Dá para usar um `conversation_event` (card centralizado) em vez de nota privada" | Poderia, mas o usuário pediu *"mensagem privada"* e o `protocolos` já fixou a convenção: **abertura → nota privada; ciclo de vida → card**. Manter a convenção é o certo (o comentário em [logic.py:1145-1150](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py) documenta a divisão) |

---

## 4. Desenho da solução

### 4.1 O evento: `kind`, título, chave e dados

| Campo | Valor | Por quê |
|---|---|---|
| `kind` | `anuncio_clicado` | casa `_KIND_RE`, não está em `RESERVED_KINDS`, e o vocabulário do CDP nesta instalação é PT-BR (`protocolo_opened` é do trackify; um kind de fora é livre) |
| `title` | `"Veio de um anúncio"` | **obrigatório** — sem ele a timeline renderiza o slug cru ([mirror.py:177-181](../../whatsbot-pro-plugins/plugins/trackify/src/mirror.py)) |
| `external_key` | **o `msg_id` (wamid) do lead** | chave natural do bloco referral, já é o dedupe do próprio `janela_72h` (`ad_leads_uq` em [002_ad_campaign.sql](../storages/plugins/janela_72h/migrations/002_ad_campaign.sql)). Torna reentrega da Meta, re-varredura e restart idempotentes |
| `occurred_at` | **`lead.ts`** (o clique), não `time.time()` | o evento pousa no lugar certo da linha do tempo mesmo emitido minutos depois — é o que permite **esperar a campanha** sem penalidade (P1) |
| `contact_id` | `contact["id"]` do WhatsBot | `phone` fica como fallback; `track_event` aceita os dois ([services.py:112](../../whatsbot-pro-plugins/plugins/trackify/src/services.py)) |
| `data` | `campaign_name`, `campaign_id`, `ad_id` (=`source_id`), `source_type`, `source_url`, `ctwa_clid`, `headline`, `channel_id` | vazios são removidos no enqueue ([mirror.py:200](../../whatsbot-pro-plugins/plugins/trackify/src/mirror.py)) |

⚠️ `ctwa_clid` é o identificador de clique que **casa a conversa com o anúncio nos relatórios da Meta**
— é o campo de maior valor analítico do conjunto e deve viajar.

### 4.2 Por que uma coluna de dedupe SEPARADA (e não `consumed_at`)

`apply_campaigns_once` ([store.py:700-767](../storages/plugins/janela_72h/store.py)) tem **três saídas
antecipadas** que existem para o `protocolos` e que seriam herdadas de graça se o Trackify entrasse no
mesmo laço:

| Linha | Situação | Efeito no laço | Efeito indevido no Trackify |
|---|---|---|---|
| [:711-712](../storages/plugins/janela_72h/store.py) | `campaign_field_key` vazio | `return` | quem não usa o rótulo do `protocolos` **nunca** teria evento — viola a D2 |
| [:713-715](../storages/plugins/janela_72h/store.py) | `protocolos` indisponível | `return` | desativar o `protocolos` calaria o Trackify |
| [:759-761](../storages/plugins/janela_72h/store.py) | `sem_protocolo` / `rotulo_inexistente` | `continue` **sem consumir** | re-emissão a cada passagem (absorvida pelo `ON CONFLICT`, mas é trabalho inútil e log sujo) |

Além disso `consumed_at` é a memória de **um** consumidor; dois consumidores compartilhando uma coluna
significa que o mais lento decide pelo outro.

**Solução:** migration `003_consumidores.sql` acrescenta **duas** colunas — uma por consumidor novo. O
mesmo raciocínio vale para a nota privada (§4.5): ela também não pode depender do `protocolos` nem do
Trackify para saber se já rodou.

```sql
ALTER TABLE plugin_janela_72h_ad_leads ADD COLUMN IF NOT EXISTS trackify_at DOUBLE PRECISION;
ALTER TABLE plugin_janela_72h_ad_leads ADD COLUMN IF NOT EXISTS note_at DOUBLE PRECISION;
CREATE INDEX IF NOT EXISTS plugin_janela_72h_ad_leads_tk
    ON plugin_janela_72h_ad_leads (trackify_at, ts);
CREATE INDEX IF NOT EXISTS plugin_janela_72h_ad_leads_nt
    ON plugin_janela_72h_ad_leads (note_at, ts);
```

Regra geral que este plano fixa para o `janela_72h`: **um consumidor, uma coluna**. Um quarto consumidor
no futuro acrescenta a sua e não toca nas outras três.

⚠️ Duas armadilhas do migrator, ambas já documentadas: **prefixo `plugin_janela_72h_` obrigatório** em
todo objeto, e **nenhum comentário pode conter `;`** — o migrator splita por `;` **antes** de remover
comentários (o aviso está escrito nas duas migrations existentes, e é uma memória do projeto).

**Backfill deliberado:** as 2 linhas existentes em produção nascem com `trackify_at IS NULL` ⇒ seriam
emitidas na primeira varredura após o deploy. Isso é **desejável** (o lead do print ganha seu evento
retroativo, com `occurred_at` correto), mas precisa ser consciente — ver P2.

### 4.3 Configuração (nasce desligada — D4)

| Chave | Default | Papel |
|---|---|---|
| `trackify_event_enabled` | `False` | interruptor do entregável **(A)** — evento na jornada do CDP |
| `campaign_note_enabled` | `False` | interruptor do entregável **(B)** — nota privada no fio |

Entra em `_CFG_DEFAULTS` ([store.py:78-93](../storages/plugins/janela_72h/store.py)), é coagida a
`bool` em `_coerce_config` ([store.py:111-134](../storages/plugins/janela_72h/store.py)), **não** entra
em `SECRET_CONFIG_KEYS` ([store.py:97](../storages/plugins/janela_72h/store.py)), e ganha um toggle na
tela de configuração + uma linha no painel de status (`GET /status`,
[routes.py:80](../storages/plugins/janela_72h/routes.py)) mostrando quantos leads foram enviados e
quantos estão pendentes.

Recomendação de UI: quando `trackify` não estiver disponível (`services.available("trackify")` falso),
o toggle aparece **desabilitado com o motivo**, em vez de sumir — o mesmo espírito do
`protocolos_bridge.available()` alimentando o seletor de rótulos ([protocolos_bridge.py:52-53](../storages/plugins/janela_72h/protocolos_bridge.py)).

### 4.4 Onde a chamada roda

| Caminho | Thread | Seguro? |
|---|---|---|
| Varredura (passo 4) | `asyncio.to_thread` ([lifecycle.py:44-57](../storages/plugins/janela_72h/lifecycle.py)) | ✅ worker thread |
| `nudge_pending` do caminho rápido | `asyncio.to_thread` ([filters.py:68](../storages/plugins/janela_72h/filters.py)) | ✅ worker thread |
| Dentro do `observe` do webhook | — | 🚫 **nunca** — a Meta reentrega se demorarmos a responder ([filters.py:79-83](../storages/plugins/janela_72h/filters.py)) |

`track_event` é síncrona, então `ServiceProxy.call` a executa direto
(`origin/developer:plugins/services.py:296-321`) sem tocar em loop nenhum.

### 4.5 A nota privada da campanha — entregável (B)

**O problema, em uma frase:** a campanha existe, está correta e é **invisível até alguém clicar em
Resolver**. O rótulo `campanha` mora no formulário de resolução do `protocolos` (o segundo print), que
é a última tela do atendimento — não a primeira.

**Precedente exato a copiar** — `protocolos._write_open_note`
([logic.py:1110-1139](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py)), que já escreve
`🔖 Protocolo aberto · PROT-…` no mesmo fio, do mesmo jeito, de dentro de um plugin:

| Passo | Chamada | Cuidado documentado no precedente |
|---|---|---|
| 1 | `deps = plugins.context.get_deps()` → `deps.agent_handler` | sai cedo se não houver handler |
| 2 | `cm = agent_handler._get_contact(phone, channel_id=channel_id)` | ⚠️ **ancorar no canal da conversa**, não no contato — contact-scoped funde canais em multicanal (plano 11) |
| 3 | `saved = cm.add_message("private_note", texto, reopen=False)` | ⚠️ usar a **linha devolvida** por `add_message`, nunca um `get_last` (numa rajada ele devolve outra mensagem) |
| 4 | `broadcast("new_message", {phone, channel_id, message: note})` | monta o dict com `_id`/`ts`/`conversation_id` vindos do `saved` |
| 5 | tudo dentro de `try/except` com `logger.debug` | uma nota que falha **nunca** pode derrubar o resto |

**Texto proposto** (uma linha; a 2ª só quando houver `headline`):

```
📣 Lead de anúncio · Campanha: [C037][COMPRA][QUENTE+ADV][11-05-2026][COMBO-SEGURANÇA
   Anúncio: 🔥 de R$ 1288 por 12x R$ 10,73 - por tempo limitado
```

O emoji na frente segue a convenção que o `protocolos` já criou (`🔖`) e é o que torna a nota
reconhecível como automação — inclusive para o filtro de histórico abaixo.

#### Três consequências do core que **precisam** ser tratadas

| # | Fato verificado | Consequência | Tratamento |
|---|---|---|---|
| 1 | `add_message` resolve a conversa com `reopen=` ([memory.py:452](../agent/memory.py)) | uma nota escrita **depois** de a conversa ser resolvida a **reabre** | passar **`reopen=False`** (mantém fechada uma conversa fechada — [memory.py:509+](../agent/memory.py)). Crítico para o backfill do P2 e para o clique que chega tarde |
| 2 | `notify_private_messages` está **`true`** em produção; com ele a nota ganha um `msg_id` sintético `pn:…` e **acende o badge de não-lida** ([memory.py:454-462](../agent/memory.py) e [:486-489](../agent/memory.py)) | a conversa **sobe e pisca como não lida** por causa de uma nota automática | é **efeito desejável** aqui (chama o atendente para um lead pago), mas precisa ser uma escolha consciente — **P6** |
| 3 | `ai_history_exclude_patterns` está **`[]`** em produção | a nota **entra no contexto do LLM**, como a do `protocolos` já entra hoje | recomendar ao operador o padrão `^private_note\t📣 Lead de anúncio` (§P7). O CLAUDE.md descreve esse filtro exatamente para "cortar notas de automação"; [history_filter.py:8-17](../agent/history_filter.py) cita o `protocolos` pelo nome |

#### Quando escrever

Mesma regra de espera da campanha do entregável (A): a nota só faz sentido **com o nome da campanha**,
e `occurred_at` não é problema porque a nota é escrita no presente (ela é um aviso ao atendente, não um
registro histórico). Na prática, com os 6-7 segundos medidos em produção, a nota aparece no fio quase
junto com a primeira mensagem do lead.

**Onde roda:** 5º passo da varredura + entrada no `nudge_pending`, exatamente como (A) — **nunca**
dentro do `observe` do webhook (§4.4).

---

## 5. Fases / Roadmap

São **dois workstreams** que só se reencontram na configuração e no release. O ramo **(B) (nota
privada) não depende da F0** — se a sincronização do core travar, ele é entregue sozinho.

```
                    ramo (A) Trackify                 ramo (B) Nota privada
                    ─────────────────                 ─────────────────────
WAVE 0   F0 (core: sync do checkout) 🔴 ─ barreira do ramo (A) SOMENTE
              │
              ├───────────────┐                              (independente)
WAVE 1   F1 (migration 003) 🟢 · F7 (teste de contrato) 🟢 ────────┐
              │               │                                    │
              ├───────────────┤                                    │
WAVE 2   F2 (trackify_bridge) 🟢                                    │
              │                                                     │
WAVE 3   F3 (passo 4: evento) 🔴 ─ depende de F1+F2         F4 (passo 5: nota) 🟢 ─ depende de F1
              └───────────────┬────────────────────────────────────┘
                              │
WAVE 4            F5 (config + UI) 🟢 · F6 (testes de integração) 🟢
                              │
WAVE 5            F8 (versão, ZIP, instalação local) 🔴
```

| Wave | Fase | Entregável | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|---|
| 0 | **F0** | (A) | core | 🔴 | médio | `plugins/services.py` existe local e a suíte do core está verde |
| 1 | **F1** | (A)+(B) | DB (plugin) | 🟢 | baixo | migration aplica; `trackify_at` e `note_at` existem |
| 1 | **F7** | (A) | testes | 🟢 | baixo | teste de contrato do `track_event` verde `[depende de: F0]` |
| 2 | **F2** | (A) | plugin (ponte) | 🟢 | baixo | `trackify_bridge.available()` responde sem levantar `[depende de: F0]` |
| 3 | **F3** | (A) | plugin (varredura) | 🔴 | médio | `[depende de: F1, F2]` evento sai uma vez e só uma |
| 3 | **F4** | (B) | plugin (varredura) | 🟢 | médio | `[depende de: F1]` nota aparece no fio uma vez e só uma |
| 4 | **F5** | ambos | config + UI | 🟢 | baixo | `[depende de: F3, F4]` os dois toggles ligam/desligam |
| 4 | **F6** | ambos | testes | 🟢 | baixo | `[depende de: F3, F4]` suíte do plugin verde |
| 5 | **F8** | ambos | release | 🔴 | baixo | ZIP determinístico + **instalado local** antes de publicar |

**Atalho se a F0 travar:** F1 → F4 → F5(parcial) → F6(parcial) → F8 entrega **(B)** sozinho, na 1.5.0,
e **(A)** vira a 1.6.0 quando o core for sincronizado.

Disciplina do repo a respeitar: **verde a cada fase**, **um refactor por commit**, nunca avançar com
teste vermelho não-explicado.

---

### Fase 0 — Sincronizar o checkout do core com `origin/developer` 🔴

**Objetivo:** trazer o seam `plugins.services` (já em produção) para o checkout local, sem o qual toda
a costura é código morto.

**Itens** *(sequenciais)*:
1. `git fetch origin developer` e revisar os 5 commits pendentes (`d0d9976`, `949525d`, `329bcbd`,
   `dc77a11`, `f087fe3`) — dois deles mexem no `trackify`, não no core.
2. Integrar em `developer` local. ⚠️ Há **trabalho não commitado no working tree** (`agent/handler.py`,
   `app/services/template_service.py`, `server/routes/channels.py`, `server/routes/conversations.py`,
   `tests/core/legacy/legacy_endpoints.py`) — decidir com o usuário se guarda em stash/branch antes.
3. Conferir que `plugins/semver.py` passa a `1.2.0` e que `docs/PLUGIN_API_CHANGELOG.md` tem a entrada
   do seam.
4. Rodar a suíte do core (`venv/bin/python -m pytest`), com atenção a
   `tests/contracts/test_plugin_services.py` e `tests/contracts/test_plugin_api_surface.py`.

⚠️ Existem **3 falhas pré-existentes** conhecidas na suíte (2 de alembic + 1 da matriz de auditoria) —
não são regressão desta fase; confirmar que são as mesmas antes/depois.

**Pronto quando:** `python -c "from plugins import services"` funciona; `plugins/loader.py` tem
`("services", _entry_services)` em `_ENTRY_SPECS`; suíte do core no Postgres de teste com o mesmo
conjunto de falhas de antes (nem uma a mais).

#### Status de execução — Fase 0
**Estado:** ✅ Concluída (2026-08-13)
- **O que foi feito:** `git merge --ff-only origin/developer` (`18d2e14` → `d0d9976`). Entraram
  `plugins/services.py` (novo, 423 linhas), `plugins/loader.py`, `plugins/manifest.py`,
  `plugins/context.py`, `plugins/semver.py` (1.1.0 → **1.2.0**), `server/app.py`,
  `docs/PLUGIN_API_CHANGELOG.md`, `CLAUDE.md`, `.claude/commands/new-plugin.md` e 5 arquivos de
  teste. 14 arquivos, +1223/−7.
- **Como foi feito / decisões:** o plano previa decidir com o usuário sobre stash do working
  tree. **Não foi preciso**: os 5 commits são um **fast-forward** e o `git diff --name-only`
  deles não intersecta nenhum dos arquivos sujos (`agent/handler.py`,
  `app/services/template_service.py`, `server/routes/channels.py`,
  `server/routes/conversations.py`, `tests/core/legacy/legacy_endpoints.py`). O WIP do usuário
  ficou intacto, sem stash e sem conflito.
- **Problemas / pendências:** nenhum.
- **Verificação:** `from plugins import services` importa; `WHATSBOT_API_VERSION == "1.2.0"`;
  `_ENTRY_SPECS` passou a ter as 9 chaves com `services` no fim; os 8 vereditos
  (`OK`/`UNAVAILABLE`/`UNKNOWN_OP`/`INCOMPATIBLE`/`DISABLED`/`WRONG_CONTEXT`/`ERROR`/`META_OP`)
  existem. Suíte do core: ver o bloco da Fase 8.

---

### Fase 1 — Migration `003_consumidores.sql` 🟢

**Objetivo:** dar a **cada** consumidor novo a própria memória, desacoplada do `protocolos` e um do
outro (§4.2). Uma migration só serve aos dois entregáveis.

**Itens** *(paralelos entre si)*:
1. `[paralelo]` Criar `migrations/003_consumidores.sql` com `ADD COLUMN IF NOT EXISTS trackify_at` **e**
   `note_at`, mais os índices `(trackify_at, ts)` e `(note_at, ts)` — todos com o prefixo
   `plugin_janela_72h_`.
2. `[paralelo]` Reler as duas migrations existentes como referência de estilo e **repetir o aviso do
   `;` em comentário**.

**Pronto quando:** `\d plugin_janela_72h_ad_leads` mostra `trackify_at` **e** `note_at`; reaplicar o
boot não erra (idempotente); `plugin_migrations` registra a `003`.

#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-08-13)
- **O que foi feito:** `migrations/003_consumidores.sql` — `ADD COLUMN IF NOT EXISTS trackify_at`
  e `note_at` (`DOUBLE PRECISION`), mais os índices `plugin_janela_72h_ad_leads_tk (trackify_at, ts)`
  e `plugin_janela_72h_ad_leads_nt (note_at, ts)`.
- **Como foi feito / decisões:** o cabeçalho documenta a regra que o plano fixa (**um consumidor,
  uma coluna**) e o porquê de não reusar `consumed_at`. Estilo das duas migrations existentes:
  sem acento, aviso do `;` em comentário repetido, prefixo `plugin_janela_72h_` em todo objeto.
- **Problemas / pendências:** nenhum.
- **Verificação:** o migrator aplicou a `003` ao subir o app de teste (o schema é criado por lá,
  não pelo migrator direto) e os 85 testes do plugin, que leem e escrevem as duas colunas, passam.
  Idempotente por construção (`IF NOT EXISTS` nas quatro instruções).

---

### Fase 2 — `trackify_bridge.py` 🟢

**Objetivo:** um módulo só, irmão do `protocolos_bridge.py`, que sabe falar com o `trackify` e mais
nada.

**Itens** *(sequenciais dentro da fase)*:
1. Import defensivo no topo (D5), no formato exato do
   [protocolos/logic.py:51-54](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py).
2. `available() -> bool` — `services.available("trackify", as_plugin="janela_72h")`, sem cache de
   falha (o plugin pode ser ligado depois; mesma justificativa do
   [protocolos_bridge.py:42-49](../storages/plugins/janela_72h/protocolos_bridge.py)).
3. `send_ad_event(lead: dict, contact: dict) -> tuple[bool, str]` — monta `kind`/`title`/`external_key`/
   `occurred_at`/`data` conforme §4.1, chama `services.call(..., _as="janela_72h")` e **traduz o
   `ServiceResult` num par `(enviou, motivo)`**, no mesmo vocabulário de motivos que o
   `protocolos_bridge.write_field` já usa ([protocolos_bridge.py:124-163](../storages/plugins/janela_72h/protocolos_bridge.py)).
4. Regra de consumo, a ser respeitada pela F3: **`DISABLED`/`UNAVAILABLE`/`INCOMPATIBLE` NÃO consomem
   o lead** (o trackify pode ser ligado depois); `OK` consome; `ERROR` não consome mas é logado em
   WARNING.
5. Cabeçalho do módulo documentando que isto é acoplamento entre plugins **consciente**, como o
   `protocolos_bridge.py` faz nas linhas 1-26.

**Pronto quando:** com o `trackify` desativado, `available()` devolve `False` e `send_ad_event`
devolve `(False, "trackify_indisponivel")` sem levantar; nenhum import de `trackify` fora do `try`.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-08-13)
- **O que foi feito:** `trackify_bridge.py` (novo) — import defensivo de `plugins.services`,
  `KIND = "anuncio_clicado"`, `TITLE = "Veio de um anúncio"`, `RETRY_REASONS` (frozenset),
  `available()`, `_external_key()`, `_event_data()`, `_reason_for()` e
  `send_ad_event(lead, contact) -> (enviou, motivo)`.
- **Como foi feito / decisões:** três coisas saíram do que o plano previa, todas para melhor:
  **(a)** `_external_key` cai em `f"lead{id}"` quando o wamid está vazio (a coluna tem
  `DEFAULT ''`) — o plano só previa o wamid, e deixar vazio faria o trackify usar o fallback
  `{contact_id}.{int(ts)}`, que **muda a cada chamada** e duplicaria o evento;
  **(b)** um envelope `OK` ainda pode trazer `{"ok": False, "reason": …}` (a op rodou e
  recusou), então a tradução distingue **recusa terminal** (`recusado:<motivo>` — contato
  inelegível, consome) de **transitória** (`sem_contato`, retenta);
  **(c)** a regra de consumo virou o frozenset `RETRY_REASONS` exportado, em vez de a F3
  reimplementar a lista — divergirem seria o mesmo bug em dois lugares.
- **Problemas / pendências:** ⚠️ **um defeito real, achado no teste de ponta a ponta (§ abaixo) e
  corrigido**: a 1ª versão tratava **toda** recusa do trackify como terminal. Com
  `mirror_contact_types` gravada no formato errado, o trackify recusa com *"tipo de contato
  'whatsapp' fora do escopo"* — erro de **configuração**, não do contato. Consumir ali perderia o
  lead, e quando o operador corrigisse a config os eventos **nunca voltariam**. Agora só são
  terminais as recusas que são propriedade IMUTÁVEL do contato (`_TERMINAL_REFUSALS`: grupo,
  identificador não-telefone, sem chat_id); tudo o mais — **inclusive um motivo desconhecido** —
  é retentável. A direção do fail-safe é deliberada: reconsultar um guard puro por até 7 dias é de
  graça, perder o evento de um lead pago não é. Nasceu também `should_retry(reason)`, para o
  chamador não fazer cirurgia de string no motivo.
- **Verificação:** 5 testes diretos da ponte — sem trackify registrado ⇒
  `(False, "trackify_indisponivel")` sem levantar; `ServiceDisabled` ⇒ `espelho_desligado`;
  recusa ⇒ `recusado:…` **fora** de `RETRY_REASONS`; contato ausente ⇒ `sem_contato` **dentro**;
  implementação que levanta ⇒ `erro`. Todos usam o **registry real** (`register_plugin_services`),
  não um mock da ponte.

---

### Fase 3 — `emit_ads_to_trackify_once()` e o 4º passo da varredura 🔴 `[depende de: F1, F2]`

**Objetivo:** consumir os leads pendentes de Trackify sem tocar no caminho do `protocolos`.

**Itens** *(sequenciais)*:
1. `store.emit_ads_to_trackify_once(limit=50) -> dict` com `{"sent", "pending", "skipped"}`, **espelhando
   a forma** de `apply_campaigns_once` ([store.py:700-767](../storages/plugins/janela_72h/store.py))
   mas com `WHERE l.trackify_at IS NULL` e o mesmo piso de idade `_AD_LEAD_MAX_AGE_SEC`
   ([store.py:73](../storages/plugins/janela_72h/store.py), 7 dias).
2. Gate de configuração no topo (`trackify_event_enabled`) e gate de disponibilidade
   (`trackify_bridge.available()`), ambos com `return` cedo — como o laço irmão faz nas
   [linhas 711-715](../storages/plugins/janela_72h/store.py).
3. Regra de espera da campanha (**P1**): emitir quando houver `campaign_name`, **ou** quando o lead já
   passou da janela de graça (recomendação: 15 min) **ou** quando o cache marcou erro definitivo — para
   que uma Graph fora do ar não faça o evento se perder. `occurred_at` continua sendo `lead.ts`, então
   a espera não desloca a linha do tempo.
4. Marcar `trackify_at = now` **somente** nos casos que a F2 definiu como consumíveis.
5. `[sequencial]` Registrar o 4º `try/except` **independente** no laço da varredura, depois do passo 3
   ([lifecycle.py:58-63](../storages/plugins/janela_72h/lifecycle.py)) — a mesma disciplina de "uma
   falha não pode impedir a outra" — e atualizar a docstring de `_sweep_loop`
   ([lifecycle.py:29-41](../storages/plugins/janela_72h/lifecycle.py)), que hoje diz "três trabalhos".
6. `[sequencial]` Acrescentar a chamada em `nudge_pending` ([store.py:772-790](../storages/plugins/janela_72h/store.py))
   para o lead recém-clicado virar evento em segundos, e ajustar a condição de saída do laço de
   degraus em [filters.py:72-73](../storages/plugins/janela_72h/filters.py) (hoje ela olha só o
   `pending` do `apply`) — **sem** deixar o laço rodar mais tempo do que os 5 degraus atuais.

**Pronto quando:** com o toggle ligado, um clique de anúncio de teste produz **uma** linha nova em
`plugin_trackify_outbox` com `kind='anuncio_clicado'` e `external_id` terminando no wamid; rodar a
varredura mais 3 vezes **não** cria nova linha; com o toggle desligado nada é criado.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída (2026-08-13)
- **O que foi feito:** em [store.py](../storages/plugins/janela_72h/store.py) — `_CONSUMER_COLUMNS`,
  `_pending_leads(column, limit)`, `_stamp(column, id, now)`, `_campaign_ready(lead, now)`,
  `_AD_CAMPAIGN_GRACE_SEC = 15 min` e `emit_ads_to_trackify_once(limit=50)`. Em
  [lifecycle.py](../storages/plugins/janela_72h/lifecycle.py) — 4º `try/except` independente e a
  docstring de `_sweep_loop` reescrita ("três trabalhos" → **cinco**). `nudge_pending` passou a
  somar os três consumidores.
- **Como foi feito / decisões:** ⚠️ **um acoplamento a mais do que o plano tinha mapeado.** A §4.2
  analisou só `apply_campaigns_once`, mas o gate de `campaign_field_key` estava **também** na
  CAPTURA (`record_ad_lead`) e na RESOLUÇÃO (`resolve_ads_once`) — quem quisesse só o evento no
  CDP não ganharia lead nenhum e a feature ficaria muda sem dizer por quê. Nasceu
  `ad_capture_enabled(cfg)` = união dos três consumidores, e cada um mantém o próprio gate
  adiante. Regra de espera (P1 (b)): emite quando há campanha, **ou** quando o lead é orgânico
  (`is_resolvable` False — nunca terá campanha), **ou** passados 15 min. O `error` do cache é
  ignorado de propósito: um erro transitório é retentado em 1 h pelo passo 2, e emitir na 1ª
  falha perderia a campanha para sempre (o evento é deduplicado por wamid).
  `nudge_pending` ganhou as chaves `sent`/`noted` e `pending` virou a **soma** — senão o laço de
  degraus do filtro desistiria com o evento e a nota ainda por fazer.
- **Problemas / pendências:** nenhum.
- **Verificação:** 11 testes. Envia com `kind`/`title`/`external_key`/`contact_id`/`data`
  corretos (inclusive `ctwa_clid`); `occurred_at == lead.ts` (não o instante do envio); 3
  varreduras seguidas ⇒ **1 chamada**; desligado ⇒ zero e `trackify_at` intacto; sem o plugin ⇒
  lead guardado; recusa terminal ⇒ consome; espera a campanha e emite na passada seguinte;
  passada a graça, emite o evento pobre; orgânico não espera.

---

### Fase 4 — Nota privada da campanha no fio 🟢 `[depende de: F1]` · **independente da F0**

**Objetivo:** o atendente vê de qual campanha o lead veio **ao abrir a conversa**, sem clicar em
Resolver.

**Itens** *(sequenciais)*:
1. `store.write_campaign_notes_once(limit=50) -> dict` com `{"written", "pending", "skipped"}`,
   espelhando a forma dos laços irmãos mas com `WHERE l.note_at IS NULL` e o mesmo piso
   `_AD_LEAD_MAX_AGE_SEC` ([store.py:73](../storages/plugins/janela_72h/store.py)).
2. Gate `campaign_note_enabled` com `return` cedo (D4).
3. Mesma regra de espera da campanha da F3 (**P1**): sem `campaign_name`, não escreve e **não** consome.
4. Escrita da nota seguindo passo a passo a tabela da §4.5 — em especial:
   **(a)** `channel_id` da **conversa**, não do contato;
   **(b)** `reopen=False`;
   **(c)** usar a linha devolvida por `add_message`;
   **(d)** `broadcast("new_message", …)` com `_id`/`ts`/`conversation_id` do `saved`;
   **(e)** `try/except` com `logger.debug` — nota que falha não derruba nada.
5. Marcar `note_at = now` **apenas** quando a nota foi de fato gravada.
6. `[sequencial]` 5º `try/except` independente no laço da varredura
   ([lifecycle.py:58-63](../storages/plugins/janela_72h/lifecycle.py)) + entrada no `nudge_pending`
   ([store.py:772-790](../storages/plugins/janela_72h/store.py)), e atualizar a docstring de
   `_sweep_loop` ([lifecycle.py:29-41](../storages/plugins/janela_72h/lifecycle.py)).

**Pronto quando:** com o toggle ligado, um clique de anúncio de teste faz aparecer **uma** nota
`📣 Lead de anúncio · Campanha: …` no fio da conversa, em segundos, sem recarregar a página (o
`broadcast` chega ao vivo); rodar a varredura mais 3× **não** cria uma segunda nota; a nota **não** é
enviada ao cliente no WhatsApp; escrever a nota numa conversa **resolvida** não a reabre.

#### Status de execução — Fase 4
**Estado:** ✅ Concluída (2026-08-13)
- **O que foi feito:** `note.py` (novo) — `PREFIX`, `RETRY_REASONS`, `build_text()` (pura),
  `_conversation_for()`, `_channel_for()`, `_add()` e
  `write_campaign_note(contact, lead) -> (escreveu, motivo)`. Em `store.py`,
  `write_campaign_notes_once(limit=50)`. Em `lifecycle.py`, o 5º `try/except` independente.
- **Como foi feito / decisões:** dois desvios conscientes do plano:
  **(a)** `_conversation_for` **nunca cria conversa** — só usa a aberta ou a última. O plano
  mandava passar `reopen=False`, e ao ler o core descobri que `reopen is False` também liga
  `create_closed=True` ([memory.py:358](../agent/memory.py)): num contato sem conversa a nota
  criaria um atendimento **já fechado**, que é o oposto do que um lead de anúncio merece. Sem
  conversa, o lead fica pendente e a próxima varredura tenta — o inbound a materializa em
  segundos, então o caminho é praticamente inalcançável, mas fecha o buraco;
  **(b)** `reopen=False` é detectado por **assinatura** (`inspect.signature`), não por
  `TypeError` — um `except TypeError` mascararia outro erro dentro do save. Num core sem o
  parâmetro a nota ainda é escrita (role painel-only já não reabre pela regra padrão).
- **Problemas / pendências:** **P6 e P7 continuam abertos e valem para produção** — ver o resumo
  ao usuário. A tela já traz a dica do padrão de `ai_history_exclude_patterns` (F5.5), mas
  aplicá-lo é decisão do operador.
- **Verificação:** 5 testes **com o app real**. ⚠️ Achado do harness: `plugins.context.get_deps()`
  é `None` nos testes de plugin (o harness não roda o lifespan, e é lá que `set_deps` é
  chamado — está escrito no [context.py:53](../plugins/context.py)). A fixture `live` publica o
  `agent_handler` que o **próprio harness construiu** pelo mesmo seam do boot e o restaura no
  fim; `ContactMemory`, `add_message`, a resolução da conversa e o `broadcast` continuam sendo
  os do core. Provado: nota aparece com a campanha e `conversation_id` preenchido; 3 varreduras
  ⇒ **1 nota**; conversa `resolved` continua `resolved` **e** recebe a nota; sem campanha não
  escreve **nem consome**; desligada não escreve.

---

### Fase 5 — Configuração e tela 🟢 `[depende de: F3, F4]`

**Objetivo:** o operador liga, desliga e enxerga o que está acontecendo.

**Itens** *(paralelos)*:
1. `[paralelo]` `trackify_event_enabled` **e** `campaign_note_enabled` em `_CFG_DEFAULTS` + coerção a
   `bool` em `_coerce_config`
   ([store.py:78-93](../storages/plugins/janela_72h/store.py), [store.py:111-134](../storages/plugins/janela_72h/store.py)).
2. `[paralelo]` Contadores no `status()` ([store.py:796](../storages/plugins/janela_72h/store.py) e a
   rota [routes.py:80](../storages/plugins/janela_72h/routes.py)): eventos enviados/pendentes, notas
   escritas/pendentes, e se o `trackify` está alcançável.
3. `[paralelo]` **Dois** toggles na tela de configuração ([static/janela_72h.js](../storages/plugins/janela_72h/static/janela_72h.js));
   o do Trackify desabilitado com motivo quando o plugin não estiver disponível (§4.3). O da nota **não**
   depende de nada externo e fica sempre habilitado.
4. `[paralelo]` O `PUT /config` ([routes.py:67](../storages/plugins/janela_72h/routes.py)) aceita as
   chaves novas; conferir que elas **não** vazam para `_mask` como se fossem segredo
   ([routes.py:34](../storages/plugins/janela_72h/routes.py)).
5. `[paralelo]` Junto do toggle da nota, uma **dica** apontando o padrão sugerido para
   `ai_history_exclude_patterns` (§P7) — o operador precisa saber que a nota entra no contexto do LLM
   se ele não cortar.

**Pronto quando:** a tela liga/desliga os dois recursos e os valores persistem; **modo escuro legível**
(classes `wa-*` / `.wa-field`, regra dura do repo); o painel de status mostra os quatro contadores.

#### Status de execução — Fase 5
**Estado:** ✅ Concluída (2026-08-13)
- **O que foi feito:** `trackify_event_enabled` e `campaign_note_enabled` em `_CFG_DEFAULTS`
  (default `False`), coeridos a `bool` em `_coerce_config` e aceitos em `set_config`; ambos em
  `_CONFIG_FIELDS` de [routes.py](../storages/plugins/janela_72h/routes.py); `campaign_status()`
  ganhou `trackify_ok`, `events_sent`/`events_pending` e `notes_written`/`notes_pending`; a tela
  ganhou o card "O que mais fazer com o clique" (dois checkboxes) e o bloco de status "Destinos
  do clique".
- **Como foi feito / decisões:** o toggle do Trackify aparece **desabilitado com o motivo** quando
  `status.trackify_ok` é falso (§4.3) — some seria pior, o operador ficaria sem saber que a opção
  existe. O da nota não depende de nada e fica sempre habilitado. Nenhuma das chaves entra em
  `SECRET_CONFIG_KEYS`, então o `_mask` não as toca. Junto do toggle da nota vai a dica do padrão
  `^private_note\t📣 Lead de anúncio` para `ai_history_exclude_patterns` (F5.5 / P7).
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --input-type=module --check` verde no `janela_72h.js`; um teste cobre os
  quatro contadores novos + `trackify_ok`. Cores: só classes `wa-*` / `.wa-field` e a tinta
  `amber-500/10` que o `custom.css` já re-tematiza — nenhum hex inline.

---

### Fase 6 — Testes de integração do plugin 🟢 `[depende de: F3, F4]`

**Objetivo:** provar a costura, não o mock.

**Itens** *(paralelos)*:
1. `[paralelo]` Teste que registra um **provider falso** chamado `trackify` no registry real
   (`services.register_plugin_services`) e verifica que `emit_ads_to_trackify_once` o chama com
   `kind='anuncio_clicado'`, `occurred_at == lead.ts` e `external_key == msg_id`.
2. `[paralelo]` Teste de **idempotência**: rodar duas vezes ⇒ uma chamada só.
3. `[paralelo]` Teste de **degradação**: sem provider registrado ⇒ nada quebra, lead **não** é
   consumido, `apply_campaigns_once` continua funcionando.
4. `[paralelo]` Teste de **independência** (a razão de ser da §4.2): com `campaign_field_key` vazio, o
   evento **ainda** sai.
5. `[paralelo]` Teste de `ServiceDisabled` (espelho desligado) ⇒ envelope `DISABLED` ⇒ lead não
   consumido.
6. `[paralelo]` **(B)** Nota escrita: uma row `role='private_note'` com o texto esperado, ligada à
   `conversation_id` certa; rodar de novo ⇒ **nenhuma** row nova (`note_at`).
7. `[paralelo]` **(B)** `reopen=False` honrado: conversa `resolved` continua `resolved` depois da nota.
8. `[paralelo]` **(B)** Independência dos dois entregáveis, nos dois sentidos: só (A) ligado ⇒ nenhuma
   nota; só (B) ligado ⇒ nenhuma chamada ao trackify.

⚠️ Teste que carrega o módulo por caminho continua **verde com a costura arrancada** — a regra do repo
é subir pelo loader/registry real. E cuidado com a armadilha do harness de plugin: ele copia o plugin
para `/tmp` mas **não muda o `cwd`**; ancore caminhos no pacote, nunca em `os.getcwd()`.

**Pronto quando:** `cd ../whatsbot-pro-plugins && python3 scripts/test_plugins.py janela_72h` verde.

#### Status de execução — Fase 6
**Estado:** ✅ Concluída (2026-08-13)
- **O que foi feito:** `tests/python/test_trackify_e_nota.py` (novo, 30 testes) no repositório de
  plugins. Dois ajustes no `test_ad_campaign.py` existente: o `clean_state` agora zera também os
  dois toggles novos (eles gateiam a captura), e `test_nudge_nunca_levanta` afere os campos em vez
  da igualdade exata do dict (que ganhou `sent`/`noted`).
- **Como foi feito / decisões:** o plano previa 8 itens; foram escritos 30, agrupados em 7 blocos.
  O provider falso é registrado no **registry real** (`services.register_plugin_services`) — um
  teste que monkeypatchasse o `trackify_bridge` continuaria verde com a costura arrancada, que é
  exatamente o que a regra do repo proíbe. Os testes de nota usam o **app real** pela fixture
  `live` (ver F4). O helper `_capturar` liga o rótulo do protocolos só para a linha entrar e o
  desliga em seguida, para cada teste começar do estado que ele quer medir.
- **Problemas / pendências:** o `clean_state` precisa apagar as `private_note` do telefone de teste
  — `messages` é tabela do CORE e o harness não a zera entre testes, então "escreveu exatamente
  uma" mediria o acumulado do módulo. Está documentado no próprio `_wipe`.
- **Verificação:** `python3 scripts/test_plugins.py --python-only janela_72h` ⇒ **85 passed**
  (55 pré-existentes + 30 novos), 13,4 s. Nenhuma regressão nos dois módulos antigos.

---

### Fase 7 — Teste de contrato do `track_event` 🟢 `[depende de: F0]` *(pode rodar já na Wave 1)*

**Objetivo:** travar as premissas que este plano assume sobre o **outro** plugin, para que uma mudança
no `trackify` apareça como teste vermelho em vez de evento que sumiu.

**Itens** *(paralelos)*:
1. `[paralelo]` `anuncio_clicado` casa `_KIND_RE` e **não** está em `RESERVED_KINDS`.
2. `[paralelo]` `SERVICES` do trackify contém a op `track_event` e `SERVICES_VERSION` está dentro do
   range que o `janela_72h` declara em `uses_services`.
3. `[paralelo]` `external_id` gerado é estável para o mesmo `external_key`.

**Pronto quando:** verde no runner do repo de plugins.

#### Status de execução — Fase 7
**Estado:** ✅ Concluída (2026-08-13)
- **O que foi feito:** bloco 1 do `test_trackify_e_nota.py` — 5 testes de contrato: o `KIND` casa
  o `_KIND_RE` que o trackify declara; não colide com nenhum `KIND_*` do `mirror`; a op
  `track_event` está no `SERVICES` publicado; o `SERVICES_VERSION` dele cabe no range que o
  `uses_services` do nosso manifest declara (medido com o `check_api_compat` do core, o **mesmo**
  motor que negocia a chamada); e o `external_key` é estável e nunca vazio.
- **Como foi feito / decisões:** os quatro primeiros leem a **fonte do trackify por AST**, sem
  importá-lo — importar puxaria httpx, o cliente do CDP e o registry inteiro dele. Sem a fonte à
  mão (clone só do core), o bloco **pula** em vez de falhar, como manda a regra do repo.
- **Problemas / pendências:** nenhuma.
- **Verificação:** verdes no runner do repositório de plugins. Se o trackify renomear a op,
  reservar `anuncio_clicado` ou mudar a versão da superfície, isto fica **vermelho** em vez de o
  evento sumir em produção.

---

### Fase 8 — Versão, ZIP e instalação 🔴

**Objetivo:** entregar sem quebrar o que já roda.

**Itens** *(sequenciais)*:
1. `janela_72h` **1.4.0 → 1.5.0**; `uses_services` no `plugin.yaml` (I2); atualizar `description`/
   `short_description` mencionando **os dois** recursos (evento no Trackify + nota privada da campanha).
   Se a F0 tiver travado e só **(B)** for entregue, a 1.5.0 descreve só a nota e o `uses_services` fica
   para a 1.6.0.
2. `python3 scripts/build_plugins.py janela_72h` e depois `--check`. ⚠️ O `--check` pode acusar
   "outdated" **falso** por `umask` (zip 644 × 664) — rebuildar para "consertar" isso é o caminho
   destrutivo.
3. **Instalar o ZIP na instância local ANTES de commitar/publicar** — a cópia viva é
   `storages/plugins/janela_72h/`, e é ela que o usuário testa.
4. Antes de publicar, `git fetch` no repo de plugins **e** conferir a tabela `plugins` de produção: uma
   versão pode ter sido publicada no meio do trabalho (já aconteceu).

**Pronto quando:** ZIP determinístico, plugin instalado local na 1.5.0, teste de ponta a ponta feito
com um clique de anúncio real ou simulado.

#### Status de execução — Fase 8
**Estado:** ✅ Concluída (2026-08-13) — **sem publicar** (não foi pedido)
- **O que foi feito:** `janela_72h` **1.4.0 → 1.5.0** no `plugin.yaml`, no `janela_72h.json` e no
  `catalog.json`; bloco `uses_services: [{plugin: trackify, version: ">=1.0,<2.0"}]` no manifest;
  `description`/`short_description` reescritas mencionando os dois destinos novos. ZIP reconstruído
  e **instalado na cópia viva** `storages/plugins/janela_72h/`.
- **Como foi feito / decisões:** `whatsbot_api_version` **continua `">=1.0,<2.0"`** de propósito —
  apesar de o seam viver na 1.2.0. Exigir 1.2 faria o plugin **não carregar** num core anterior e
  levaria junto a etiqueta de 72h, a campanha no protocolos e a nota, que não dependem dele. O
  parser do manifest lê só chaves conhecidas via `data.get()`, então um core antigo ignora o
  `uses_services` e a ponte degrada para indisponível — que é o comportamento desejado.
  A instalação foi por swap atômico (extrair ao lado → `mv` → remover o antigo).
- **Problemas / pendências:** **nada foi commitado nem publicado** — o usuário não pediu, e o
  repositório de plugins tem WIP de terceiros (`whatsapp_cloud`, `lembretes`) no mesmo working
  tree. O teste de ponta a ponta **foi feito** (seção abaixo) depois que o usuário instalou o
  trackify em dev; o ZIP foi **reconstruído** após o fix da F2 (sha256 `2efe8a2f…`) e reinstalado.
- **Verificação:** `build_plugins.py --check` ⇒ `current` (15 arquivos, 45959 bytes, sha256
  `a73dedfe…`); `diff -rq src/ storages/plugins/janela_72h` ⇒ **byte-idênticos**; a cópia instalada
  reporta `version: 1.5.0` e traz `note.py`, `trackify_bridge.py` e `migrations/003_consumidores.sql`.
  `git fetch` no repositório de plugins ⇒ em dia com o remoto; tabela `plugins` de **produção**
  consultada ⇒ `janela_72h` **1.4.0**, `trackify` 4.0.0, `protocolos` 1.33.0, todos sem
  `load_error` — nenhuma versão publicada no meio do trabalho.

---

### Teste de ponta a ponta REAL (2026-08-13) — ✅ feito

O usuário instalou o **trackify 4.0.0 nesta instância de dev**, o que destravou a validação que
estava pendente. Um script carregou os plugins **instalados** (`storages/plugins/`) pelo loader e
pelo registry de serviços de verdade — a mesma costura do boot — contra o banco de dev:

| Medida | Resultado |
|---|---|
| `services.available("trackify", as_plugin="janela_72h")` | **True** |
| Superfície publicada | `trackify` v`1.0.0`, 18 ops, **`track_event` entre elas** |
| `emit_ads_to_trackify_once()` | `{"sent": 1, "pending": 0, "skipped": 0}` |
| Linha em `plugin_trackify_outbox` | `kind='anuncio_clicado'`, `status='pending'`, `external_id='wb.<install_id>.ext.anuncio_clicado.wamid.…'`, `title="Veio de um anúncio"`, payload com `campaign_name`/`campaign_id`/`ad_id`/`ctwa_clid`/`source_url`/`headline`/`channel_id` |
| 2ª passada | `{"sent": 0, …}`, fila **1 → 1** (idempotente) |

**Segurança:** nenhuma credencial de produção foi copiada. O dispatcher do trackify só envia ao CDP
quando `ingestion_url` está setada ([lifecycle.py:53-55](../storages/plugins/trackify/lifecycle.py)),
e essa chave não existe em dev — o evento ficou na fila e não saiu da máquina. Tudo o que o script
criou (lead, cache, contato, linha da fila, as 3 chaves de config) foi removido no fim.

⚠️ **Gotcha que custou uma rodada e virou o bug corrigido na F2:** `mirror_contact_types` é lido como
**string CSV**, não como lista (`mirror._allowed_types` faz `str(valor).split(",")`). Gravar
`["whatsapp"]` produz o conjunto `{"['whatsapp']"}` e **todo contato é recusado**. Produção grava a
string `"whatsapp"` — correto.

### Resultado da suíte do core (barreira da F0)

`venv/bin/python -m pytest -q` ⇒ **3 FAILED, 0 ERROR**, 100% da coleta:
`test_alembic_hygiene.py::test_linear_chain_single_parent_reaches_all_revisions`,
`::test_no_unexpected_duplicate_sequence_prefixes` e
`test_audit_characterization.py::test_audit_matrix_is_complete` — exatamente as **3 falhas
pré-existentes** já registradas, nem uma a mais.

⚠️ A primeira rodada acusou **21 falhas**, com 14 `ERROR` de `relation "contacts" does not exist`.
**Não era regressão**: é a assinatura de duas execuções de pytest concorrentes sobre o mesmo
`whatsbot_test` (cada processo faz `init_test_engine(reset=True)`, que **derruba e recria o schema
`public`**). Uma rodada isolada, com `pg_stat_activity` confirmando sessão única, estabilizou nas 3.
Registre-se para a próxima vez: **nunca duas suítes Postgres em paralelo**, e o concorrente pode
estar em outra máquina.

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Checkout do core atrás | Escrever a ponte e ela nunca executar (o `entry.services` é ignorado em silêncio) | F0 é barreira; validar com `from plugins import services` antes de qualquer outra fase |
| Working tree sujo na F0 | Merge do core misturado com WIP de `template_service`/`channels`/`conversations` | Stash/branch antes; decidir com o usuário |
| Evento duplicado | Timeline do CDP poluída | `external_key` = wamid (§4.1) + `ON CONFLICT (external_id) DO NOTHING` + coluna `trackify_at` |
| Acoplamento ao `protocolos` | Desligar o `protocolos` calaria o Trackify | Laço **separado** com dedupe própria (§4.2) — travado pelo teste F5.4 |
| Backfill das 2 linhas de produção | Eventos retroativos aparecendo "do nada" | É desejável e `occurred_at` os põe na data certa; ainda assim, decidir em **P2** e avisar o usuário |
| Chamada na thread errada | Bloquear o event loop / `WRONG_CONTEXT` | Só de dentro de `asyncio.to_thread` (§4.4); **nunca** dentro do `observe` do webhook |
| `mirror_enabled` desligado | Evento vira `DISABLED` silencioso | Não consumir o lead nesse veredito (F2.4) + expor o estado no painel (F4.2) |
| Migration | `;` em comentário quebra o migrator; prefixo errado é recusado | Regra repetida na F1; ambos já documentados nas migrations existentes |
| `RESERVED_KINDS` crescer | Um `kind` do trackify novo colidir com `anuncio_clicado` | Teste de contrato F6.1 fica vermelho na hora |
| **(B)** Nota reabre conversa resolvida | Atendimento fechado volta a aparecer como aberto | `reopen=False` (§4.5 nº1) — travado pelo teste F6.7 |
| **(B)** Nota entra no contexto do LLM | A IA "vê" o texto da automação e pode comentá-lo com o cliente | Padrão em `ai_history_exclude_patterns` (P7); hoje o filtro está `[]` em produção e a nota do `protocolos` **já** entra — o plano só não pode piorar isso em silêncio |
| **(B)** Nota acende badge de não-lida | Conversa pisca como não lida por causa de automação | Efeito de `notify_private_messages=true` (§4.5 nº2); decidir em **P6**, não descobrir em produção |
| **(B)** `channel_id` errado | Nota cai no canal errado em instalação multicanal | Ancorar na **conversa**, não no contato — o precedente do `protocolos` documenta isso explicitamente (plano 11) |
| **(B)** `get_last` racy | Nota transmitida com o id/ts de outra mensagem | Usar a linha devolvida por `add_message` (§4.5 nº3) |
| Modo escuro | Toggle novo ilegível | Classes `wa-*`/`.wa-field`; conferir com o tema escuro ligado |
| Restart de plugin | Toggle derruba o processo; estado em memória se perde | Estado só em `plugin_janela_72h_*` (já é o caso) |
| Segredo | `ctwa_clid`/`headline` viajam para o CDP | Não são segredo (são dado de campanha do próprio anunciante); os segredos do plugin continuam só `meta_ads_token`/`meta_app_secret` e **não** entram no payload |

---

## 7. Perguntas em aberto

**P1 — Esperar a campanha ou emitir na hora do clique?**
⏸️ **ADIADO — recomendação abaixo, confirmar com o usuário.**
Contexto: o `referral` chega com `source_id` (id do **anúncio**), e o nome da campanha só existe depois
da consulta à Graph (passo 2 da varredura). Em produção isso levou **6 e 7 segundos**.
(a) Emitir imediatamente, sem campanha — evento pobre, e um segundo evento depois seria pior ainda.
(b) Esperar a campanha, com janela de graça de ~15 min e emissão mesmo assim se a Graph falhar.
**Recomendação: (b)** — como `occurred_at` é o `ts` do clique, esperar **não** desloca a linha do tempo,
e o caso comum resolve em segundos. É a escolha que dá o evento mais rico sem risco de perdê-lo.

**P2 — Emitir retroativamente os leads já capturados?**
⏸️ **ADIADO — decisão do usuário.**
Hoje são **2 linhas** em produção (13/08, ambas com campanha resolvida), uma delas o contato do print.
(a) Deixar `trackify_at IS NULL` ⇒ a primeira varredura pós-deploy emite os 2 (o piso de 7 dias de
`_AD_LEAD_MAX_AGE_SEC` já limita naturalmente).
(b) Backfillar `trackify_at = now` na migration ⇒ só cliques novos viram evento.
**Recomendação: (a)** — o volume é 2, o `occurred_at` põe cada um no dia certo, e o usuário vê o
resultado no mesmo contato do print. Se o volume fosse alto, (b) seria a resposta.

**P3 — O `kind` deve ser PT-BR (`anuncio_clicado`) ou EN (`ad_click`)?**
⏸️ **ADIADO — cosmético, mas irreversível na prática** (o `kind` participa do `external_id`; trocar
depois duplicaria a linha no CDP).
Os kinds internos do trackify são EN (`protocolo_opened`, `conversation_created` —
[mirror.py:33-46](../../whatsbot-pro-plugins/plugins/trackify/src/mirror.py)), mas o `title` que o
operador lê é PT-BR. **Recomendação: `anuncio_clicado`**, com `title` "Veio de um anúncio" — o campo
que aparece na tela é o `title`, e o slug fica coerente com o resto da instalação. Decidir **antes** da
F2.

**P4 — Um evento por clique ou um por janela de 72h?**
✅ **DECIDIDO (2026-08-13): um por clique.** É o que o pedido descreve ("quando ele vier de anúncio da
meta") e o que a chave natural (wamid) entrega. Um lead que clica em dois anúncios diferentes vira dois
eventos — que é a informação correta para o funil.

**P5 — O `janela_72h` deve auditar a entrega (`plugins.context.audit`)?**
⏸️ **ADIADO.** A regra do repo manda auditar "ação com efeito externo", e isto é uma. Mas o
`trackify` **já audita** as escritas dele (`contact.fields_written`, `consent.applied` —
[services.py:304-307](../../whatsbot-pro-plugins/plugins/trackify/src/services.py)) e `track_event`
deliberadamente **não** audita (é enfileiramento, alto volume). **Recomendação: não auditar** no
`janela_72h` — seria uma linha por clique de anúncio, e o registro de verdade é a própria fila
`plugin_trackify_outbox`. A nota privada, idem: ela **é** o próprio registro visível.

**P6 — A nota privada deve acender o badge de não-lida?**
⏸️ **ADIADO — decisão do usuário, e ela tem efeito imediato em produção.**
Contexto: `notify_private_messages` está **`true`** em produção, então toda `private_note` ganha um
`msg_id` sintético `pn:…` e participa do encanamento de não-lida
([memory.py:454-462](../agent/memory.py), [:486-489](../agent/memory.py)).
(a) Deixar como está — a conversa do lead pago **pisca** na sidebar. É atenção bem gasta num lead que
custou dinheiro, e o comportamento é o mesmo que a nota do `protocolos` já tem hoje.
(b) Passar um `msg_id` próprio (não-`pn:`) para escapar do gate e escrever a nota "silenciosa".
**Recomendação: (a)** — consistência com o `protocolos` e o alerta é desejável. Mas é preciso o usuário
saber **antes**, porque muda o que ele vê na sidebar no dia do deploy.

**P7 — Cortar a nota do contexto do LLM?**
⏸️ **ADIADO — recomendação forte: sim.**
Em produção `ai_history_exclude_patterns` é **`[]`**, ou seja, nada é cortado hoje e a nota
`🔖 Protocolo aberto · PROT-…` do `protocolos` **já entra** no contexto do agente. Acrescentar uma
segunda nota de automação piora um problema existente. Sugestão de padrão (Configurações → IA, uma
regex por linha):
```
^private_note\t📣 Lead de anúncio
^private_note\t🔖 Protocolo aberto
```
Não é mudança de código — é configuração — mas **pertence ao plano** porque é consequência direta dele.
Decidir se entra como recomendação na tela (F5.5) ou se o executor aplica junto do deploy.

**P8 — Nota também para lead ORGÂNICO (`source_type='post'`)?**
⏸️ **ADIADO.** `is_resolvable` ([ad_referral.py:70-75](../storages/plugins/janela_72h/ad_referral.py))
já separa `post` (publicação orgânica, **não tem campanha**) de `ad`. Hoje, sem campanha, não haveria
texto para a nota.
(a) Nada para orgânico (o que o plano assume).
(b) Nota degradada — `📣 Lead de publicação · <headline>` — sem campanha.
**Recomendação: (a) por ora**, com (b) como incremento barato depois. Em produção os 2 leads existentes
são ambos `ad`, então não há dado para justificar (b) agora.

---

## 8. Apêndice — arquivos-chave

**Core (só sincronização, F0 — nada escrito por este plano)**
- `plugins/services.py` *(vem de `origin/developer`, 423 linhas)*
- `plugins/loader.py` — `_entry_services` (283-292), `_ENTRY_SPECS` (325-338)
- `plugins/manifest.py` — `uses_services` (82, 221)
- `server/app.py` — registro no boot (177-184)
- `plugins/semver.py` — `WHATSBOT_API_VERSION` 1.1.0 → 1.2.0

**Plugin `janela_72h`** — fonte em `../whatsbot-pro-plugins/plugins/janela_72h/src/`, cópia viva em
[storages/plugins/janela_72h/](../storages/plugins/janela_72h/) *(hoje byte-idênticas)*
- `plugin.yaml` — versão 1.5.0 + `uses_services` **[novo bloco]**
- `trackify_bridge.py` **[arquivo novo]** — entregável (A)
- `note.py` **[arquivo novo]** *(ou função em `store.py`)* — entregável (B)
- [store.py](../storages/plugins/janela_72h/store.py) — `_CFG_DEFAULTS` (78-93), `_coerce_config`
  (111-134), `nudge_pending` (772-790), `status` (796) + `emit_ads_to_trackify_once` **[nova]** +
  `write_campaign_notes_once` **[nova]**
- [lifecycle.py](../storages/plugins/janela_72h/lifecycle.py) — `_sweep_loop` (29-68), 4º e 5º passos
- [filters.py](../storages/plugins/janela_72h/filters.py) — condição de saída dos degraus (72-73)
- [routes.py](../storages/plugins/janela_72h/routes.py) — `_mask` (34), `PUT /config` (67), `/status` (80)
- [static/janela_72h.js](../storages/plugins/janela_72h/static/janela_72h.js) — dois toggles
- `migrations/003_consumidores.sql` **[arquivo novo]** — `trackify_at` + `note_at`
- `tests/python/test_trackify_bridge.py` **[arquivo novo]**
- `tests/python/test_campaign_note.py` **[arquivo novo]**

**Core consultado pelo entregável (B) — somente LEITURA**
- [agent/memory.py](../agent/memory.py) — `add_message` (436-501), `reopen`/`_resolve_conversation` (452),
  gate `notify_private_messages` (454-462, 486-489)
- [agent/history_filter.py](../agent/history_filter.py) — `CONFIG_KEY` (38), motivação (8-17)
- [db/repositories/_mapping.py](../db/repositories/_mapping.py) — `private_note` é preview-excluded (105)

**Plugin `trackify` (somente LEITURA — não muda nada)**
- `src/services.py` — `track_event` (96-124), guards (71-93), `SERVICES` (366-385)
- `src/mirror.py` — `eligible` (145-166), `enqueue` (171-222), `KIND_*` (33-46), `TITLES` (61-73)
- `src/migrations/001_initial.sql` — `external_id` (21, 46)

**Referências de padrão (leitura)**
- [storages/plugins/janela_72h/protocolos_bridge.py](../storages/plugins/janela_72h/protocolos_bridge.py) — o irmão desta ponte
- `../whatsbot-pro-plugins/plugins/protocolos/src/logic.py` — import defensivo (51-54), `services.call` (5313-5316)
- `../whatsbot-pro-plugins/plugins/protocolos/src/plugin.yaml` — `uses_services` (204-206)

---

## 9. Checklist de verificação

- [x] `from plugins import services` funciona no checkout local (F0)
- [x] Suíte do core verde no Postgres de teste (`WHATSBOT_TEST_DB_URL`), com as **mesmas 3 falhas
      pré-existentes** e nem uma a mais
- [x] `python3 scripts/test_plugins.py janela_72h` verde no repo de plugins — **85 passed**
- [x] Migration aplica **e reaplica** sem erro (round-trip, `IF NOT EXISTS` nas 4 instruções);
      prefixo `plugin_janela_72h_` em todo objeto; nenhum `;` dentro de comentário
- [x] **(A)** Clique de anúncio ⇒ **exatamente uma** linha `kind='anuncio_clicado'` em
      `plugin_trackify_outbox`, com `external_id` terminando no wamid — **validado de ponta a ponta**
      contra o trackify 4.0.0 real (ver "Teste de ponta a ponta REAL")
- [x] **(A)** Rodar a varredura 3× depois ⇒ **nenhuma** chamada nova
- [x] **(B)** Clique de anúncio ⇒ **exatamente uma** nota `📣 Lead de anúncio · Campanha: …` no fio
- [x] **(B)** Rodar a varredura 3× depois ⇒ **nenhuma** nota nova
- [x] **(B)** A nota **não** chega ao cliente no WhatsApp (role `private_note`, painel-only)
- [x] **(B)** Nota em conversa `resolved` **não** a reabre (`reopen=False`)
- [ ] **(B)** A nota aparece **ao vivo** (broadcast), sem recarregar a página — *só verificável no
      painel; o harness não tem ws_manager*
- [x] Os dois toggles: desligado ⇒ nada acontece; ligado ⇒ acontece — e um **não** depende do outro
- [x] `trackify` desativado ⇒ `janela_72h` continua marcando a etiqueta, gravando a campanha **e
      escrevendo a nota** (D2 + independência)
- [x] `campaign_field_key` vazio ⇒ o evento e a nota **ainda** saem (§4.2) — e a **captura** também,
      que era o acoplamento extra achado na F3
- [ ] Decidido o que fazer com `ai_history_exclude_patterns` (P7) — hoje `[]` em produção.
      **Decisão do usuário**; a tela já traz o padrão sugerido
- [x] Tela de configuração legível no **modo escuro** (só `wa-*` / `.wa-field` + `amber-500/10`,
      que o `custom.css` re-tematiza; nenhum hex inline)
- [x] Restart do plugin (toggle) não perde estado (tudo em `plugin_janela_72h_*`)
- [x] Nenhum segredo em URL, log ou payload (`meta_ads_token`/`meta_app_secret` continuam os únicos
      em `SECRET_CONFIG_KEYS` e não entram no `data` do evento)
- [x] ZIP determinístico (`--check` diz `current`, sha256
      `a73dedfe…`, 15 arquivos) e **plugin instalado localmente** na 1.5.0
- [x] Conferida a tabela `plugins` de produção antes de publicar — segue **1.4.0**, nada foi
      publicado no meio do trabalho
