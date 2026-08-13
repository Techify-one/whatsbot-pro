# Plano 120 — A cobrança PIX paga vira COMPRA na jornada do Trackify (e o produto sai de um cadastro)

> **Status:** PLANEJAMENTO · **Data:** 2026-08-13 · **Escopo:** médio
> **Origem:** pedido do usuário — *"Ainda preciso fazer a liberação do curso, e pensei em fazer isso em
> outro lugar, mas para isso preciso enviar o evento de pagamento para algum lugar. Esse lugar vai ser o
> trackify (via plugin interno), pois nele consigo criar automações com outro módulo para liberar o curso
> quando o cliente pagar. […] nesse campo de produto pode ter uma lista de itens (e também um botão para
> cadastro na config do plugin) […] quando o cliente pagar, chega webhook no whatsbot e envia para o
> trackify no contato informando valor (deve somar com o que o cliente gastou), nome do produto, etc."*
> É a continuação do **plano 114**, cuja pergunta **P2** ("lista de produtos ou descrição livre?") ficou
> ⏸️ ADIADA com a nota *"a lista vira uma aba de configuração depois, sem mudar schema"*.
> **Método:** leitura do código real (core + `pagamentos` + `trackify`) **e consulta ao CDP de produção**
> (`banco-nexus-redes-brasil`, leitura). Todo `arquivo:linha` abaixo foi verificado nesta sessão; os
> números do CDP são medidos, não estimados.
>
> **O achado que reorganiza o plano: a ponte para o Trackify já existe e está MORTA — em silêncio.**
> [trackify_bridge.py:87](../../whatsbot-pro-plugins/plugins/pagamentos/src/trackify_bridge.py) chama
> `services.call("trackify", "track_event", kind=…, **payload_achatado)`, mas a assinatura real é
> `track_event(kind, *, contact_id, phone, data, occurred_at, external_key, title)`
> ([trackify/src/services.py:96](../../whatsbot-pro-plugins/plugins/trackify/src/services.py)). Todo
> `txid=`/`valor=`/`descricao=` é kwarg inesperado ⇒ `TypeError` ⇒ `ServiceProxy.call` embrulha em
> `ServiceResult(status="error")` ([plugins/services.py:319-321](../plugins/services.py)) ⇒ a ponte
> **ignora o retorno** e loga em `debug`. **Confirmado no CDP: zero eventos `pix_*`** — só
> `conversation_*`, `protocolo_*` e `contact_*`. O plano 114 marcou a Fase 9 como ✅ com a ressalva
> *"o efeito no CDP depende do trackify em execução"*; o efeito nunca existiu.
>
> **A segunda metade do trabalho não é código: é configuração na tela do Trackify.** O canal `whatsbot`
> no CDP **não tem mapeamento para `value` e não tem NENHUMA linha em `channel_value_rules`** — sem
> isso, valor nenhum soma no "total gasto", por mais correta que a chamada fique. A §7 lista exatamente
> o que criar lá.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar
> para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| D1 | ✅ (2026-08-13) O pagamento entra no CDP como **`event_type = purchase`** — o MESMO balde das vendas Ticto | Uma automação *"comprou o produto X → libera o curso"* cobre **checkout e atendimento de uma vez**. Não se cria um tipo paralelo que exigiria duplicar toda automação. O `purchase` **não** está em `RESERVED_KINDS` e passa no `_KIND_RE` ([mirror.py:33-46](../../whatsbot-pro-plugins/plugins/trackify/src/mirror.py), [services.py:88-95](../../whatsbot-pro-plugins/plugins/trackify/src/services.py)) |
| D2 | ✅ (2026-08-13) Estorno usa **`refunded`** com efeito **`subtract`**, espelhando o Ticto | Mesma nomenclatura que o CDP já tem regra para interpretar no canal `ticto`. Cresce o escopo (F5) porque hoje o plugin não detecta devolução |
| D3 | ✅ (2026-08-13) O produto viaja **só como `product_name`** — sem `product_id` | Justificado por medição: `PRODUCT_IDENTITY_FIELDS = ("product_name", "offer_name", "product_id", "offer_id")` ([_config.py:91](../../whatsbot-pro-plugins/plugins/trackify/src/_config.py)) e a chave do produto é o **nome** ([journey.py:382-416](../../whatsbot-pro-plugins/plugins/trackify/src/journey.py)). ⚠️ **Consequência dura:** o nome cadastrado precisa bater **exatamente** com o do Ticto, senão vira produto paralelo na jornada (§5, R1) |
| D4 | ✅ (2026-08-13) Cadastro de produtos é **tabela própria do plugin**, sem coluna do Nexus | `plugin_pagamentos_produtos` (nome, valor padrão, ativo). O módulo de produtos/ofertas do Nexus (`produtos_produtos`/`produtos_ofertas`, que **existem** e têm 30+ cursos) fica para outro momento, por decisão explícita do usuário |
| D5 | ✅ (2026-08-13) O `data` do evento é **generoso e estável** | Manda tudo que a cobrança sabe. Chave sem mapeamento é descartada de graça pelo CDP e ainda sobrevive no `wb_raw` — então **acrescentar um campo depois é só criar o mapeamento na tela, sem release do plugin**. Foi a pergunta literal do usuário: *"o plugin do trackify consegue enviar campos de forma dinâmica para eu não precisar mexer no código?"* → **sim** (§2.2) |
| D6 | ✅ (2026-08-13) A configuração do CDP é aplicada **manualmente pelo usuário na UI do Trackify** | O plano **documenta** o passo a passo (§7) e **não** escreve em `channel_mappings`/`channel_value_rules`. O CDP é outro produto: o WhatsBot não configura o CRM alheio |
| D7 | ✅ (2026-08-13) O modal mantém a **descrição livre**; a lista de produtos é opcional | Escolher produto preenche descrição + valor (ambos editáveis); quem não escolher digita como hoje. Cobrança avulsa (acerto, parcela quebrada) continua possível |
| D8 | ✅ Regra do repo — **tudo que pode ficar no plugin fica no plugin** (CLAUDE.md §"O que fica no core e o que vai pro plugin") | **Zero mudança no core.** O seam `plugins.services` já existe no checkout ([plugins/services.py](../plugins/services.py), commit `949525d`) — ao contrário do plano 119, que precisou de uma Fase 0 de sincronização |

---

## 1. Resumo executivo

Hoje uma cobrança PIX paga morre dentro do WhatsBot: aparece como nota privada no fio, muda para "Pago"
na tela do plugin — e o CDP nunca fica sabendo. O contato que acabou de comprar continua com "Total
gasto: R$ 0,00" e nenhuma automação consegue reagir ao pagamento.

O conserto tem **três metades desiguais**:

1. **Consertar a ponte** (pequeno, mas é o que destrava tudo): a chamada existe, está escrita errada e
   falha em silêncio há uma versão inteira. Passa a mandar `data={…}` + `title` + `external_key`
   idempotente, e passa a **olhar o `ServiceResult`** — foi justamente a ausência disso que escondeu o
   bug.
2. **Dar um cadastro de produtos ao plugin** (médio): tabela + aba na configuração + lista no modal,
   moldados nos vendedores, que já fazem exatamente isso.
3. **Detectar devolução** (o que mais cresce): o Inter não avisa "expirou" nem é reconsultado depois de
   pago — a varredura só olha cobranças `ATIVA`. Para `refunded` existir é preciso uma segunda janela de
   reconsulta sobre as pagas recentes.

E uma quarta parte que **não é código**: criar, na tela do Trackify, o mapeamento de valor e as duas
regras que fazem o dinheiro somar. Sem essa etapa, o evento chega bonito e vale R$ 0,00.

---

## 2. Como funciona hoje (mapa)

### 2.1 A ponte quebrada

O módulo [trackify_bridge.py](../../whatsbot-pro-plugins/plugins/pagamentos/src/trackify_bridge.py) é
bem-intencionado e defensivo em tudo — menos na forma da chamada:

| Linha | O que faz | Veredito |
|---|---|---|
| [:31-33](../../whatsbot-pro-plugins/plugins/pagamentos/src/trackify_bridge.py) | `KIND_GERADO/PAGO/EXPIRADO = "pix_gerado"/"pix_pago"/"pix_expirado"` | Kinds válidos, não reservados — mas nenhum chegou ao CDP |
| [:59-79](../../whatsbot-pro-plugins/plugins/pagamentos/src/trackify_bridge.py) | monta `payload` **achatado** (txid, status, descricao, cliente_nome, valor, …) e emite no bus | O `_emit_bus` funciona (é o único efeito real hoje) |
| [:87](../../whatsbot-pro-plugins/plugins/pagamentos/src/trackify_bridge.py) | `_services.call("trackify", "track_event", _as=…, kind=kind, **payload)` | ⛔ **`TypeError`**: `track_event` não aceita `txid`, `valor`, `descricao`… |
| [:88-91](../../whatsbot-pro-plugins/plugins/pagamentos/src/trackify_bridge.py) | comentário diz *"o resultado é ignorado: trackify ausente/desligado devolve UNAVAILABLE/DISABLED, que é degradação limpa"* | A premissa está certa; o efeito colateral é que **`error` também é ignorado** — o bug ficou invisível |

Chamadores: [routes.py:236](../../whatsbot-pro-plugins/plugins/pagamentos/src/routes.py) (`cobranca_gerada`),
[reconcile.py:104](../../whatsbot-pro-plugins/plugins/pagamentos/src/reconcile.py) (`cobranca_paga`) e
[reconcile.py:116](../../whatsbot-pro-plugins/plugins/pagamentos/src/reconcile.py) (`cobranca_expirada`).

**Por que falha em silêncio** — [plugins/services.py:319-321](../plugins/services.py):

```python
return self._envelope(OK, op, data=fn(**kw))
except BaseException as e:   # noqa: BLE001 — total isolation is the contract
    return self._failure(op, e)
```

O isolamento total é o contrato do seam (correto). Quem chama é que precisa olhar o envelope.

### 2.2 O que o `trackify` aceita — e por que campo dinâmico já funciona

Assinatura real ([services.py:96-98](../../whatsbot-pro-plugins/plugins/trackify/src/services.py)):

```python
def track_event(kind: str, *, contact_id=None, phone: str = "", data: dict | None = None,
                occurred_at: float | None = None, external_key: str = "",
                title: str = "") -> dict
```

| Fato verificado | Onde | Consequência |
|---|---|---|
| `data` é repassado **inteiro**, sem allowlist de chaves — só filtra `None`/`""` | [mirror.py:201](../../whatsbot-pro-plugins/plugins/trackify/src/mirror.py) | ✅ **Qualquer chave nova viaja sem tocar no código do `trackify`** — responde a pergunta do usuário (D5) |
| `title` vindo de fora é **obrigatório na prática** | [mirror.py:177-181](../../whatsbot-pro-plugins/plugins/trackify/src/mirror.py) — *"um `kind` vindo de fora PRECISA passar o seu, senão renderiza como slug cru"* | Sem `title`, a timeline do atendente mostra `purchase` |
| `external_key` vira `external_id` (`wb.<install>.ext.<kind>.<chave>`); default = `{contact_id}.{int(ts)}` | [services.py:118-124](../../whatsbot-pro-plugins/plugins/trackify/src/services.py) | Passar o **txid** torna o evento idempotente entre webhook e varredura |
| `enqueue` devolve `False` quando a linha já existia | [mirror.py:171-181](../../whatsbot-pro-plugins/plugins/trackify/src/mirror.py) | A dedupe é no `external_id` — o mesmo pagamento nunca duplica |
| `_need_mirror()` levanta `ServiceDisabled` com `mirror_enabled=False` | [services.py:71-74](../../whatsbot-pro-plugins/plugins/trackify/src/services.py) | Degradação limpa (status `disabled`), não erro |
| `eligible()` recusa grupo e tipo de contato fora de `mirror_contact_types` | [mirror.py:145-166](../../whatsbot-pro-plugins/plugins/trackify/src/mirror.py) | Cobrança em grupo nunca vira compra — correto, e já é o comportamento |

### 2.3 O canal `whatsbot` no CDP (medido em produção)

Os quatro campos **nativos** do evento já estão mapeados — de graça para nós:

| `source_expression` | destino | Situação |
|---|---|---|
| `kind` | `event.event_type` | ✅ já existe |
| `title` | `event.title` | ✅ já existe |
| `external_id` | `event.external_id` | ✅ já existe |
| `occurred_at` | `event.occurred_at` | ✅ já existe |
| `$string($)` | `event.wb_raw` | ✅ já existe — guarda o payload cru inteiro |

E **estes `data.*` também já estão mapeados** (herdados dos eventos de protocolo/atendimento), o que
significa que basta o plugin usar o **mesmo nome de chave** para o campo aterrissar sem configuração
nova: `atendente`, `canal`, `conversation_id`, `motivo`, `origem`, `protocolo_id`, `etiquetas`,
`nota`, `sugestao`, `aberto_por`, `alterados`, `cadastro_*`, `campo_*`.

⛔ **O que NÃO existe no canal `whatsbot`:**

- **nenhum** mapeamento com destino `event.value` — nada nesse canal pode virar dinheiro;
- **zero** linhas em `channel_value_rules` (o `whatsbot-teste` tem 5, todas `ignore`).

### 2.4 Como o CDP decide que um evento é dinheiro (receita medida nos canais que funcionam)

| Canal | Mapeamento de valor | `sum_to_total_spent` | Regras |
|---|---|---|---|
| `ticto` | `order.paid_amount / 100` → `event.value` | ✅ | `purchase`→**add**, `authorized`→add, `active_subscription`→add, `refunded`/`chargeback`→**subtract**, 9× `ignore` |
| `pagarme` | `data.paid_amount / 100` → `event.value` | ✅ | `charge.paid`→**add**, `charge.refunded`→**subtract**, 6× `ignore` |
| `whatsbot` | — nenhum — | — | — nenhuma — |

São **duas** engrenagens em série: a coluna `channel_mappings.sum_to_total_spent` marca *qual mapeamento
carrega dinheiro* e a `channel_value_rules.effect` decide *o que aquele tipo de evento faz com ele*.
Faltando qualquer uma, `contacts.total_spent` não se move.

### 2.5 Como o CDP identifica o produto (o que valida a D3)

[`_product_identity`](../../whatsbot-pro-plugins/plugins/trackify/src/journey.py) (`journey.py:382-416`)
percorre `PRODUCT_IDENTITY_FIELDS` **em ordem** e para no primeiro preenchido; a chave final é
`subscription_id or nome`. Como `product_name` é o **primeiro** da lista ([_config.py:91](../../whatsbot-pro-plugins/plugins/trackify/src/_config.py))
e o `_identity_map` (`journey.py:362`) traduz `product_id → product_name` para os eventos que só trazem
id, **os dois caminhos convergem no nome**. Mandar só `product_name` unifica com a venda Ticto — desde
que a grafia bata (§5, R1).

Os campos de evento necessários **já existem** no CDP (54 cadastrados). Reaproveitáveis para o PIX:
`product_name`, `offer_name`, `product_id`, `vendedor`, `transaction_id`, `payment_method`, `status`,
`canal`, `conversation_id`, `motivo`, `atendente`, `utm_term`, `fee`, `installments`.

### 2.6 O ciclo de vida da cobrança no plugin (onde o estorno vai doer)

| Etapa | Onde | Nota |
|---|---|---|
| Criar | [routes.py:142-266](../../whatsbot-pro-plugins/plugins/pagamentos/src/routes.py) | `descricao` livre → `solicitacaoPagador` (≤140, sem acento, [mensagem.py:58-69](../../whatsbot-pro-plugins/plugins/pagamentos/src/mensagem.py)) **e** `produto` → `infoAdicionais` ([inter.py:209-224](../../whatsbot-pro-plugins/plugins/pagamentos/src/inter.py)) |
| Conferir | [reconcile.py:60-124](../../whatsbot-pro-plugins/plugins/pagamentos/src/reconcile.py) | Regra ÚNICA de "está paga?", com 3 chamadores (webhook, varredura, botão). Sempre reconsulta `GET /pix/v2/cob/{txid}` ([inter.py:245-247](../../whatsbot-pro-plugins/plugins/pagamentos/src/inter.py)) |
| Pagamento | `_paid_entry` ([reconcile.py:29-36](../../whatsbot-pro-plugins/plugins/pagamentos/src/reconcile.py)) lê `cob_remota["pix"][0]` | ⭐ **É o mesmo lugar onde vive `devolucoes[]` no padrão do BACEN** — detectar estorno **não exige endpoint novo** |
| Varredura | `pending_for_reconcile` ([store.py:339-345](../../whatsbot-pro-plugins/plugins/pagamentos/src/store.py)) filtra `status = ATIVA` | ⚠️ **Cobrança paga sai da varredura para sempre** — uma devolução posterior nunca seria vista. É o coração da F5 |
| Estados | `STATUS_ATIVA/CONCLUIDA/EXPIRADA` ([store.py:65-67](../../whatsbot-pro-plugins/plugins/pagamentos/src/store.py)) | Falta `DEVOLVIDA` |

---

## 3. Inventário do trabalho

| # | Item | Onde | O que falta | Risco | Esforço |
|---|---|---|---|---|---|
| 1 | Ponte com a assinatura certa | [trackify_bridge.py:59-102](../../whatsbot-pro-plugins/plugins/pagamentos/src/trackify_bridge.py) | `data={…}`, `title`, `external_key=txid`, `contact_id`/`phone` fora do `data` | baixo | S |
| 2 | Olhar o `ServiceResult` | idem `:87` | Logar `warning` quando `status ∉ {ok, unavailable, disabled}` — a cegueira que escondeu o bug | baixo | S |
| 3 | Kinds `purchase`/`refunded` | idem `:31-33` | Renomear o pago para `purchase`; acrescentar `refunded`. `pix_gerado`/`pix_expirado` continuam (P2) | baixo | S |
| 4 | Tabela de produtos | `migrations/003_produtos.sql` (novo) | `plugin_pagamentos_produtos` (nome, valor_padrao, ativo, created_at) | baixo | S |
| 5 | Coluna `produto_nome` na cobrança | `migrations/003_produtos.sql` | Snapshot do nome na cobrança — igual ao `vendedor_nome` do plano 116 | baixo | S |
| 6 | CRUD de produtos (store) | [store.py:159-198](../../whatsbot-pro-plugins/plugins/pagamentos/src/store.py) como molde | `list/get/create/update/delete_produto` | baixo | S |
| 7 | CRUD de produtos (rotas) | [routes.py:347-392](../../whatsbot-pro-plugins/plugins/pagamentos/src/routes.py) como molde | `/produtos` GET/POST/PUT/DELETE + `audit` | baixo | S |
| 8 | `metadata` devolve produtos | [routes.py:89-112](../../whatsbot-pro-plugins/plugins/pagamentos/src/routes.py) | Lista de ativos, como já faz com vendedores | baixo | S |
| 9 | `criar_cobranca` aceita `produto_id` | [routes.py:175-182](../../whatsbot-pro-plugins/plugins/pagamentos/src/routes.py) | Resolver, gravar `produto_nome`, semear descrição/valor | baixo | M |
| 10 | Select de produto no modal | [CobrancaModal.js:199-214](../../whatsbot-pro-plugins/plugins/pagamentos/src/static/CobrancaModal.js) | Select acima da descrição (molde: o de vendedor, `:208-214`) que preenche descrição + valor | baixo | M |
| 11 | Aba de produtos na configuração | [config.js:302-331](../../whatsbot-pro-plugins/plugins/pagamentos/src/static/config.js) | Bloco igual ao "Vendedores" (`criarVendedor` `:124`, `removerVendedor` `:130`) | baixo | M |
| 12 | Detectar devolução | [reconcile.py:29-36](../../whatsbot-pro-plugins/plugins/pagamentos/src/reconcile.py) | Ler `devolucoes[]` do mesmo `pix[0]` | médio | M |
| 13 | Janela de reconsulta pós-pago | [store.py:339-345](../../whatsbot-pro-plugins/plugins/pagamentos/src/store.py) | 2ª consulta: `CONCLUIDA` com `paid_at` recente (P4) | **alto** | M |
| 14 | Estado `DEVOLVIDA` + colunas | `migrations/004_devolucao.sql` (novo) | `refunded_at`, `valor_devolvido`, `devolucao_id` | médio | S |
| 15 | Testes | `tests/python/` (3 arquivos hoje) | Contrato da assinatura + puros do produto + devolução | baixo | M |
| 16 | Configuração no Trackify | tela do CDP | §7 — 7 mapeamentos + 2 regras (+1 campo opcional) | médio | S |
| 17 | Publicar e instalar | repo de plugins | O `pagamentos` **não está em produção** | médio | S |

### 3.1 Falsos positivos descartados

| "Problema" | Por que NÃO é |
|---|---|
| "Precisa de `product_id` para a automação achar o curso" | Não. `product_name` é o **primeiro** da precedência e a chave do produto é o nome ([_config.py:91](../../whatsbot-pro-plugins/plugins/trackify/src/_config.py), [journey.py:382-416](../../whatsbot-pro-plugins/plugins/trackify/src/journey.py)). Foi o que permitiu a D3 |
| "Precisa mexer no plugin `trackify` para mandar campo novo" | Não. `data` é repassado inteiro, sem allowlist ([mirror.py:201](../../whatsbot-pro-plugins/plugins/trackify/src/mirror.py)). Quem decide o aterrissamento é o mapeamento na tela — configuração, não código |
| "Precisa criar vários campos de evento no CDP" | Quase nenhum. Dos 54 já cadastrados, `product_name`, `vendedor`, `transaction_id`, `payment_method`, `status`, `canal`, `conversation_id`, `motivo` e `atendente` cobrem tudo. Só `e2e_id` seria novo — e é opcional (§7) |
| "Precisa de evento/filtro novo no core" | Não. Zero mudança no core: o seam `plugins.services` já está no checkout ([plugins/services.py](../plugins/services.py)) e `uses_services: trackify` já está no manifesto ([plugin.yaml](../../whatsbot-pro-plugins/plugins/pagamentos/src/plugin.yaml)) |
| "Os `pix_gerado` antigos vão duplicar/conflitar" | Não. **Zero eventos `pix_*` no CDP** (medido) — a ponte nunca entregou nada. Não há histórico para migrar nem dedupe a fazer |
| "`INTEGER PRIMARY KEY AUTOINCREMENT` quebra no Postgres" | Não. O migrator traduz para `SERIAL PRIMARY KEY` ([plugins/migrator.py:39,106](../plugins/migrator.py)). A migration 001 do próprio plugin já usa esse idiom |
| "Precisa de endpoint novo do Inter para ver devolução" | Não para **detectar**: `devolucoes[]` vive dentro de `pix[]`, no mesmo `GET /cob/{txid}` que a conferência já faz. O custo real é a **janela de reconsulta** (item 13), não a chamada |
| "A ponte precisa virar assíncrona para não travar o loop" | Não. `services.call` chamado de worker thread faz a ponte sozinho; e os 3 call sites já rodam em `asyncio.to_thread` ([plugins/services.py:296-321](../plugins/services.py)) |

---

## 4. Fases / Roadmap

```
WAVE 0   F0 (caracterização: provar a ponte morta)  🔴  [bloqueia: F1]
            │
WAVE 1   F1 (ponte + kinds)  🟢 [dep F0]      ·   F2 (cadastro: migration+store+rotas)  🟢
            │                                          │
WAVE 2   F5 (devolução)  🔴 [dep F1]   ·   F3 (modal)  🟢 [dep F2]   ·   F4 (config UI)  🟢 [dep F2]
            │
WAVE 3   F6 (testes + suíte verde)  🔴 [dep F1,F2,F3,F4,F5]
            │
WAVE 4   F7 (configurar o Trackify)  🔴 [dep F1]   ·   F8 (publicar + instalar)  🔴 [dep F6,F7]
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | F0 | Caracterização | 🔴 | baixo | Teste **vermelho** que prova o `TypeError` de hoje |
| 1 | F1 | Ponte | 🟢 `[dep: F0]` | baixo | F0 fica verde; evento aparece na jornada |
| 1 | F2 | Cadastro (backend) | 🟢 | baixo | `GET/POST/PUT/DELETE /produtos` respondem |
| 2 | F3 | Modal | 🟢 `[dep: F2]` | baixo | Escolher produto preenche descrição + valor |
| 2 | F4 | Configuração (UI) | 🟢 `[dep: F2]` | baixo | Cadastrar/remover produto pela aba Configurar |
| 2 | F5 | Devolução | 🔴 `[dep: F1]` | **alto** | Estorno vira `refunded` e desconta |
| 3 | F6 | Testes | 🔴 `[dep: F1–F5]` | baixo | Runner do plugin verde |
| 4 | F7 | Trackify (UI) | 🔴 `[dep: F1]` | médio | "Total gasto" sobe após um PIX real |
| 4 | F8 | Publicar/instalar | 🔴 `[dep: F6,F7]` | médio | `pagamentos` ativo em produção |

---

### Fase 0 — Caracterização: provar que a ponte está morta 🔴

**Objetivo:** transformar o diagnóstico em teste, antes de tocar em qualquer linha.

**Itens**
1. Em `tests/python/`, teste que registra um provedor **fake** `trackify` cuja op `track_event` tem a
   **mesma assinatura** da real ([services.py:96-98](../../whatsbot-pro-plugins/plugins/trackify/src/services.py)),
   e chama `trackify_bridge.cobranca_paga({...})` — `[sequencial]`
2. Assere que **hoje** o provedor **não é invocado com sucesso** (o envelope volta `error`): é o retrato
   do bug, e vira o critério de aceite da F1 — `[sequencial]`
3. Não corrigir nada nesta fase. Um commit só de teste — `[sequencial]`

**Pronto quando:** o teste roda e falha **pela razão certa** (kwargs inesperados), não por import.

#### Status de execução — Fase 0
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase 1 — A ponte passa a falar a língua do `track_event` 🟢 `[depende de: F0]`

**Objetivo:** o pagamento vira um evento de compra real na jornada.

**Itens**
1. Reescrever `track()` ([trackify_bridge.py:59-91](../../whatsbot-pro-plugins/plugins/pagamentos/src/trackify_bridge.py))
   separando **identidade** (`contact_id`, `phone` — argumentos próprios) de **dado** (`data={…}`) — `[sequencial]`
2. `data` generoso e **com os nomes que o canal já mapeia** (§2.3) — `[sequencial]`:

   | chave em `data` | valor | por quê esse nome |
   |---|---|---|
   | `valor` | só em `purchase`/`refunded` | vira `event.value` (§7) |
   | `produto_nome` | snapshot da cobrança | → `product_name` |
   | `descricao` | descrição livre | sem mapeamento hoje; sobrevive no `wb_raw` |
   | `txid` / `e2e_id` | identificadores do PIX | `transaction_id` / campo novo opcional |
   | `vendedor_nome` / `vendedor_utm` | cadastro do plugin | → `vendedor` / `utm_term` |
   | `forma_pagamento` | constante `"pix"` | → `payment_method` |
   | `atendente` | `created_by_name` | ✅ **já mapeado** |
   | `canal` / `conversation_id` | da cobrança | ✅ **já mapeados** |
   | `motivo` | só em `refunded` | ✅ **já mapeado** |
3. `title` explícito, senão a timeline mostra o slug cru ([mirror.py:177-181](../../whatsbot-pro-plugins/plugins/trackify/src/mirror.py)) — P1 — `[sequencial]`
4. `external_key = txid` (e `f"{txid}.dev.{devolucao_id}"` no estorno): idempotência entre webhook e
   varredura sem lógica própria, porque `enqueue` dedupa pelo `external_id` — `[sequencial]`
5. `occurred_at` = `paid_at` da cobrança, não `time.time()`: um pagamento reconciliado horas depois tem
   de cair na hora certa da timeline — `[sequencial]`
6. **Olhar o retorno**: `warning` quando `status ∉ {ok, unavailable, disabled}`; os dois últimos são
   degradação normal (trackify ausente/desligado) — `[sequencial]`
7. Kinds: `purchase` (pago) e `refunded` (estorno); `pix_gerado`/`pix_expirado` continuam **sem valor**
   (P2) — `[paralelo]`
8. `_emit_bus` **continua** emitindo `pagamentos.<kind>` — é observabilidade para quem assina `"*"`
   (`debug_bus`), o mesmo precedente do `protocolos` — `[paralelo]`

**Pronto quando:** o teste da F0 fica **verde**; num contato de teste com o `trackify` ativo, pagar uma
cobrança de R$ 1,00 faz aparecer **um** evento na aba Jornada com título legível (ainda **sem** somar
valor — isso é a F7).

#### Status de execução — Fase 1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase 2 — Cadastro de produtos (migration + store + rotas) 🟢

**Objetivo:** o produto deixa de ser texto digitado e passa a ser escolhido.

**Itens**
1. `migrations/003_produtos.sql` — `[sequencial]`
   - `plugin_pagamentos_produtos (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL, valor_padrao NUMERIC(12,2), ativo INTEGER NOT NULL DEFAULT 1, created_at DOUBLE PRECISION NOT NULL DEFAULT 0)`
   - `ALTER TABLE plugin_pagamentos_cobrancas ADD COLUMN IF NOT EXISTS produto_nome TEXT` (snapshot, mesma
     lógica do `vendedor_nome` da migration 002)
   - ⚠️ **Sem `;` em comentário** — o migrator quebra o arquivo por ponto-e-vírgula ANTES de remover
     comentários (aviso literal no cabeçalho de [001_initial.sql](../../whatsbot-pro-plugins/plugins/pagamentos/src/migrations/001_initial.sql))
   - ⚠️ Índice único **case-insensitive** no nome? → P6
2. `store.py`: `list_produtos(only_active=)`, `get_produto`, `create_produto`, `update_produto`,
   `delete_produto` — molde literal dos vendedores ([store.py:159-198](../../whatsbot-pro-plugins/plugins/pagamentos/src/store.py)) — `[paralelo]`
3. `routes.py`: `/produtos` GET/POST/PUT/DELETE com `plugin_permission("view"/"manage")` e `audit`,
   molde [routes.py:347-392](../../whatsbot-pro-plugins/plugins/pagamentos/src/routes.py) — `[sequencial]`
4. `metadata` ([routes.py:89-112](../../whatsbot-pro-plugins/plugins/pagamentos/src/routes.py)) passa a
   devolver `produtos` (só ativos), ao lado de `vendedores` — `[sequencial]`
5. `criar_cobranca` aceita `produto_id` opcional, resolve pelo cadastro e grava `produto_nome` na linha
   ([routes.py:175-231](../../whatsbot-pro-plugins/plugins/pagamentos/src/routes.py)); produto
   desconhecido ⇒ 400, como o vendedor já faz — `[sequencial]`
6. O `produto_nome` **snapshot** é o que viaja ao CDP: renomear ou excluir o produto depois não reescreve
   a história nem obriga um join no caminho do webhook — `[sequencial]`

**Pronto quando:** dá para criar/listar/editar/excluir produto pela API; criar cobrança com `produto_id`
grava `produto_nome`; migration roda limpa em banco novo **e** em banco já existente.

#### Status de execução — Fase 2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase 3 — O modal ganha a lista de produtos 🟢 `[depende de: F2]`

**Objetivo:** escolher o produto em vez de digitar — e com isso garantir a grafia que o CDP vai casar.

**Itens**
1. Estado `produtoId` em [CobrancaModal.js:31-38](../../whatsbot-pro-plugins/plugins/pagamentos/src/static/CobrancaModal.js) — `[sequencial]`
2. `<select>` **acima** do campo de descrição (`:199-204`), molde visual do select de vendedor
   (`:208-214`), com a opção `"Sem produto (descrever)"` como primeira — `[sequencial]`
3. Escolher produto preenche **descrição** (nome) e **valor** (`valor_padrao`), ambos ainda editáveis;
   editar depois **não** desfaz a escolha — `[sequencial]`
4. `produto_id` entra no corpo do POST (`:69-70`, ao lado de `vendedor_id`) — `[sequencial]`
5. Lista vazia ⇒ o select **não aparece** (instalação sem cadastro fica idêntica à de hoje) — `[paralelo]`
6. ⚠️ Modo escuro: usar `.wa-field` e classes `wa-*`, como o resto do modal já faz — `[paralelo]`

**Pronto quando:** com produtos cadastrados, escolher um preenche descrição e valor e a cobrança nasce
com `produto_nome`; sem cadastro nenhum, o modal é byte-idêntico ao atual.

#### Status de execução — Fase 3
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase 4 — Aba de produtos na configuração do plugin 🟢 `[depende de: F2]`

**Objetivo:** cadastrar produto onde o usuário pediu — dentro do "Configurar" do próprio plugin.

**Itens**
1. Bloco "Produtos" em [config.js](../../whatsbot-pro-plugins/plugins/pagamentos/src/static/config.js),
   irmão do "Vendedores" (`:302-331`), com `criarProduto`/`removerProduto` moldados em `:124-133` — `[sequencial]`
2. Campos: nome (obrigatório) e valor padrão (opcional) — `[sequencial]`
3. Carregar junto no `Promise.all` do `carregar()` (`:73-77`) — `[sequencial]`
4. Texto de ajuda curto e explícito: **o nome precisa ser idêntico ao do produto no Ticto**, senão a
   jornada mostra dois produtos diferentes (§5, R1) — `[paralelo]`
5. ⚠️ Regra do repo: **nada disso vai para o painel de Configurações do core** — é a aba do plugin — `[paralelo]`

**Pronto quando:** dá para cadastrar e remover produto pela aba Configurar, e o novo produto aparece no
modal sem recarregar a página inteira. Legível no modo escuro.

#### Status de execução — Fase 4
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase 5 — Devolução vira `refunded` 🔴 `[depende de: F1]`

**Objetivo:** dinheiro que voltou deixa de contar como receita no CDP.

> ⚠️ **É a fase de maior risco do plano** e a única que mexe no caminho quente do pagamento. Se apertar o
> prazo, entregue F1–F4 + F7 (que já resolvem o pedido original) e trate esta como tranche própria.

**Itens**
1. `_devolucoes(pago)` puro, irmão de `_paid_entry` ([reconcile.py:29-36](../../whatsbot-pro-plugins/plugins/pagamentos/src/reconcile.py)):
   lê `pix[0]["devolucoes"]` e devolve a primeira com `status == "DEVOLVIDO"` — `[sequencial]`
2. `migrations/004_devolucao.sql`: `refunded_at`, `valor_devolvido`, `devolucao_id` + `STATUS_DEVOLVIDA`
   em [store.py:65-67](../../whatsbot-pro-plugins/plugins/pagamentos/src/store.py) — `[paralelo]`
3. `store.mark_refunded(txid, …)` com a mesma disciplina de transição do `mark_paid` (`:290-310`):
   devolve `True` só na primeira vez — `[sequencial]`
4. **A janela de reconsulta** (o item difícil): `pending_for_reconcile` (`:339-345`) só olha `ATIVA`.
   Acrescentar uma segunda consulta — `CONCLUIDA` com `paid_at` dentro da janela (P4) e `checked_at`
   antigo — sem transformar a varredura num full scan da tabela — `[sequencial]`
5. `trackify_bridge.cobranca_devolvida(cob)` com `valor` = **valor devolvido** (não o valor da cobrança:
   devolução parcial existe) e `motivo` — `[sequencial]`
6. Nota privada de estorno no fio, molde `notify.aviso_pago` ([notify.py:355-366](../../whatsbot-pro-plugins/plugins/pagamentos/src/notify.py)) — P5 — `[paralelo]`
7. `broadcast_cobranca(cob, event="devolvido")` para a tela reagir ao vivo (`:368`) — `[paralelo]`
8. Investigar **antes de codificar** se o Inter empurra webhook de devolução (P3): se empurrar, o
   `process_webhook_payload` ([reconcile.py:140](../../whatsbot-pro-plugins/plugins/pagamentos/src/reconcile.py))
   vira o caminho rápido e a varredura fica só como rede de segurança — `[sequencial]`

**Pronto quando:** devolver um PIX de teste pelo app do Inter faz a cobrança virar "Devolvida", nasce a
nota privada e o CDP recebe `refunded`; o "Total gasto" do contato **volta ao valor anterior**.

#### Status de execução — Fase 5
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase 6 — Testes 🔴 `[depende de: F1–F5]`

**Objetivo:** travar o que foi consertado, para não regredir em silêncio de novo.

**Itens**
1. **Contrato da assinatura** (o mais valioso): provedor fake com a assinatura real; assere `kind`,
   `title`, `external_key`, `contact_id` e as chaves de `data` — é o teste que teria pego este bug — `[paralelo]`
2. `purchase` leva `valor`; `pix_gerado`/`pix_expirado` **não** levam — `[paralelo]`
3. Idempotência: webhook + varredura no mesmo pagamento ⇒ **um** `external_key` — `[paralelo]`
4. Puros do produto: resolução de `produto_id`, snapshot do nome, produto inexistente ⇒ 400 — `[paralelo]`
5. Devolução: parse de `devolucoes[]`, transição única, valor devolvido no evento — `[paralelo]`
6. Degradação: sem `plugins.services`, com trackify desligado e com trackify ausente, **nada levanta** e
   o pagamento acontece igual — `[paralelo]`
7. Rodar `python3 scripts/test_plugins.py pagamentos` no repo de plugins — `[sequencial]`

**Pronto quando:** runner do plugin verde; suíte do core verde no Postgres (o plugin não mexe no core,
mas a regra do repo é não avançar com vermelho não-explicado).

#### Status de execução — Fase 6
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase 7 — Configurar o canal `whatsbot` no Trackify 🔴 `[depende de: F1]`

**Objetivo:** fazer o valor **somar**. Sem esta fase, tudo o que veio antes vale R$ 0,00.

**Itens** (execução manual do usuário — passo a passo completo na **§7**)
1. Criar o campo de evento `e2e_id` (opcional) — `[paralelo]`
2. Criar os **7 mapeamentos** no canal `whatsbot`, sendo o de `data.valor → value` o único com **somar ao
   total gasto** ligado — `[sequencial]`
3. Criar as **2 regras de valor**: `purchase → add`, `refunded → subtract` — `[sequencial]`
4. Conferir na tela que os tipos antigos (`conversation_*`, `protocolo_*`, `contact_*`) ficam como
   `ignore` — eles não carregam `valor`, mas o explícito evita ambiguidade (§5, R3) — `[sequencial]`
5. Repetir no canal `whatsbot-teste` antes do de produção, se quiser ensaiar — `[paralelo]`

**Pronto quando:** um PIX de R$ 1,00 pago faz o "Total gasto" do contato subir R$ 1,00 e a compra aparece
com o **nome do produto** na jornada.

#### Status de execução — Fase 7
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase 8 — Publicar e instalar 🔴 `[depende de: F6, F7]`

**Objetivo:** sair do dev. **O `pagamentos` não está instalado em produção** — lá só rodam `trackify
4.0.0`, `protocolos 1.33.0` e `janela_72h 1.4.0` (medido).

**Itens**
1. `version: "1.1.0"` no manifesto (MINOR: recurso novo, nada quebrado) — `[sequencial]`
2. Instalar a versão nova **no dev** e conferir na interface **antes** de commitar/publicar — é a regra
   do repo, e o que roda é `storages/plugins/pagamentos`, não o `src/` — `[sequencial]`
3. `python3 scripts/build_plugins.py pagamentos` + `--check`; catálogo antes do build — `[sequencial]`
4. ⚠️ Antes de afirmar paridade, **conferir a tabela `plugins` de produção**: versão publicada no meio do
   trabalho é armadilha conhecida deste repo — `[sequencial]`
5. Importar o `.zip` em produção e ativar; conferir credenciais do Inter e chave PIX do ambiente — `[sequencial]`
6. F7 precisa estar feita **antes** do primeiro pagamento real, senão a primeira venda entra sem valor e
   fica torta na jornada (o evento é idempotente por `external_key` — reprocessar não conserta) — `[sequencial]`

**Pronto quando:** `pagamentos` aparece ativo e sem `load_error` na tabela `plugins` de produção, e uma
cobrança real de valor baixo percorre o ciclo inteiro até somar no CDP.

#### Status de execução — Fase 8
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

## 5. Riscos e cuidados

| # | Ponto | Risco | Mitigação |
|---|---|---|---|
| R1 | Grafia do produto (D3) | `"BGP do Zero"` no plugin e `"BGP DO ZERO"` no Ticto ⇒ **dois produtos** na jornada e a automação erra o alvo | Cadastro em vez de digitação (F2–F4) + aviso na tela (F4) + P6 (índice único e/ou normalização). Conferir a grafia na jornada de um contato que já comprou pelo Ticto **antes** de cadastrar |
| R2 | Mapeamento de valor no canal | O mapeamento vale para **todo** evento do canal `whatsbot` | Só `purchase`/`refunded` mandam a chave `valor`; os demais kinds continuam sem ela — a mesma disciplina que o plano 114 já documentou ("cobrança gerada não é receita") |
| R3 | Canal sem nenhuma regra hoje | Ao criar as duas primeiras regras, o canal muda de "sem regras" para "com regras" — **a confirmar** se tipos não listados passam a ser tratados como `ignore` | Nenhum outro evento do canal carrega valor, então não há dinheiro a ganhar ou perder. Ainda assim, conferir a tela após criar (F7, item 4) e comparar com o `ticto`, que lista todos os tipos explicitamente |
| R4 | Idempotência do evento | Webhook + varredura + botão "Conferir" podem coincidir; sem chave estável, três eventos e **triplo do valor** | `external_key = txid` (F1, item 4); a dedupe é do `enqueue` ([mirror.py:171-181](../../whatsbot-pro-plugins/plugins/trackify/src/mirror.py)), não do plugin |
| R5 | Janela de reconsulta (F5) | Varrer todas as `CONCLUIDA` a cada ciclo vira full scan à medida que a tabela cresce | Janela curta por `paid_at` + `checked_at` antigo + `limit`, reusando o índice `status, expira_at` ou criando um por `status, paid_at` |
| R6 | Devolução parcial | Mandar o valor da cobrança em vez do valor devolvido **zera** uma venda que só teve 10% estornado | O `refunded` leva `valor_devolvido`; teste dedicado (F6, item 5) |
| R7 | Migration de plugin | Comentário com `;` quebra o arquivo (o migrator divide por `;` **antes** de remover comentários) | Revisar 003/004 antes de rodar; o cabeçalho da 001 já traz o aviso |
| R8 | Restart do plugin | Ativar/atualizar plugin derruba o processo (`os._exit`) | Nada de estado em variável de módulo; tudo em `plugin_pagamentos_*` — o plugin já segue isso |
| R9 | Contato inelegível | Cobrança em grupo ou canal fora de `mirror_contact_types` ⇒ `eligible()` recusa e o evento some | Degradação esperada e correta. O `warning` da F1 item 6 torna isso **visível** em vez de mudo |
| R10 | Segredo em log/auditoria | `pix_code` é o instrumento de pagamento | Já tratado no plugin ([routes.py:230-236](../../whatsbot-pro-plugins/plugins/pagamentos/src/routes.py) exclui do diff); **não** acrescentar `pix_code` ao `data` do evento |

---

## 6. Perguntas em aberto

**P1 — Qual `title` do evento de compra?**
Sem `title` a timeline mostra `purchase` cru. (a) `"Compra por PIX — <produto>"`; (b) só `"Compra por
PIX"`; (c) o nome do produto puro (é o que o `ticto` faz, mapeando `offer.name → title`).
**Recomendação: (a)** — a origem (atendimento, não checkout) é justamente o que o Ticto não diz, e o
produto aparece junto. ⏸️ Confirmar na F1.

**P2 — `pix_gerado` e `pix_expirado` continuam indo ao CDP?**
(a) Sim, sem valor — a timeline mostra "cobrança enviada" e "cobrança venceu", útil para o vendedor;
(b) não, só `purchase`/`refunded`, deixando a jornada enxuta.
**Recomendação: (a)** — não custa nada (sem `valor`, não viram dinheiro) e agora terão `title` legível.
⏸️ Confirmar na F1.

**P3 — O Banco Inter empurra webhook de devolução?**
Muda o desenho da F5: com webhook, a varredura vira só rede de segurança; sem ele, a janela de
reconsulta (item 13) é o **único** caminho. O plugin hoje só registra webhook de PIX recebido
([inter.py:252-262](../../whatsbot-pro-plugins/plugins/pagamentos/src/inter.py)).
**A confirmar na documentação/sandbox do Inter — não assumir.** ⏸️

**P4 — Janela de reconsulta pós-pagamento.**
(a) 30 dias; (b) 90 dias (o prazo de MED do PIX é maior); (c) configurável.
**Recomendação: (a) 30 dias como default configurável** — cobre o caso real (estorno é rápido) sem virar
varredura eterna. ⏸️ Decidir na F5.

**P5 — Nota privada quando o dinheiro volta?**
**Recomendação: sim** — o atendente precisa saber que a venda caiu, e o precedente (`aviso_pago`) já
existe. ⏸️ Decidir na F5.

**P6 — Como impedir dois produtos com o mesmo nome escrito diferente?**
(a) índice único **case-insensitive** (`LOWER(nome)`) e nada mais; (b) normalizar acento/caixa na
escrita — perde a grafia exata que o Ticto usa, e a grafia exata é justamente o que importa (D3);
(c) nada, só o aviso de tela.
**Recomendação: (a)** — barra o duplicado óbvio e **preserva** a grafia. ⏸️ Decidir na F2.

**P7 — Semear o cadastro a partir do catálogo do Nexus?**
`produtos_produtos` tem os cursos com o nome canônico e resolveria a R1 de vez. A D4 excluiu o Nexus
desta rodada.
**Recomendação: fora de escopo aqui**, anotado como o próximo passo natural quando o módulo de
produtos/ofertas entrar. ⏸️ ADIADO

---

## 7. ⭐ O que criar na tela do Trackify (execução manual — Fase 7)

> Tudo abaixo é no **canal `whatsbot`** do Trackify. Nada disso é feito por código (D6).
> A ordem importa: **campo → mapeamento → regra**.

### 7.1 Campos de evento — quase nada a fazer

Os campos que o PIX precisa **já existem** no CDP e serão reaproveitados: `product_name`, `vendedor`,
`transaction_id`, `payment_method`, `status`, `canal`, `conversation_id`, `motivo`, `atendente`,
`utm_term`.

| Criar? | Campo | Nome sugerido | Tipo |
|---|---|---|---|
| ➕ **opcional** | `e2e_id` | `ID da transação PIX (E2E)` | Texto |

> Se preferir não criar nada, pule o `e2e_id`: o identificador ponta-a-ponta continua guardado no
> `wb_raw` e o `txid` (que é o que você usa para conciliar) já vai em `transaction_id`.

### 7.2 Mapeamentos do canal `whatsbot` — **7 a criar**

| # | Origem (`source_expression`) | Destino | Somar ao total gasto |
|---|---|---|---|
| 1 | `data.valor` | evento → **`value`** | ✅ **SIM** ← *a única com isto ligado* |
| 2 | `data.produto_nome` | evento → `product_name` | não |
| 3 | `data.vendedor_nome` | evento → `vendedor` | não |
| 4 | `data.vendedor_utm` | evento → `utm_term` | não |
| 5 | `data.txid` | evento → `transaction_id` | não |
| 6 | `data.forma_pagamento` | evento → `payment_method` | não |
| 7 | `data.e2e_id` | evento → `e2e_id` | não *(só se criou o campo em 7.1)* |

**Já existem — não recriar:** `kind → event_type`, `title → title`, `external_id`, `occurred_at`,
`$string($) → wb_raw`, `data.atendente`, `data.canal`, `data.conversation_id`, `data.motivo`.
É por isso que o plugin usa **exatamente esses nomes** de chave (F1, item 2).

### 7.3 Regras de valor do canal `whatsbot` — **2 a criar** (hoje há zero)

| Tipo de evento | Efeito | Por quê |
|---|---|---|
| `purchase` | **add** (somar) | O PIX pago entra no mesmo balde da venda Ticto (D1) |
| `refunded` | **subtract** (subtrair) | Devolução tira o dinheiro do total (D2) |

> ⚠️ **`refunded` NÃO é um campo — é o tipo do evento.** Não existe `data.refunded` e **o estorno não
> precisa de mapeamento nenhum**: o `refunded` viaja no `kind` (já mapeado para `event_type`) e o valor
> devolvido viaja na **mesma chave `data.valor`** do mapeamento nº 1. Quem inverte o sinal é a REGRA, não
> o campo — exatamente como o canal `ticto`, que tem **um único** mapeamento de valor
> (`order.paid_amount / 100 → value`) servindo `purchase`→add e `refunded`/`chargeback`→subtract.
>
> ```
> kind=purchase   data.valor = 1997.00  → regra add      → +R$ 1.997,00
> kind=refunded   data.valor =  500.00  → regra subtract → −R$   500,00   (devolução parcial)
> ```
>
> O `data.valor` do estorno leva o **valor devolvido**, nunca o da cobrança (R6); o motivo vai em
> `data.motivo`, que também já está mapeado.

Depois de criar, confira na tela que os tipos antigos do canal (`conversation_created`,
`conversation_resolved`, `conversation_reopened`, `protocolo_opened`, `protocolo_closed`,
`protocolo_rated`, `protocolo_fields_updated`, `contact_updated`, `contact_tagged`, `contact_untagged`)
aparecem como **`ignore`** — nenhum deles carrega `valor`, mas o explícito evita ambiguidade (R3).

### 7.4 Teste de aceitação (faça nesta ordem)

1. Cadastre um produto no plugin com o nome **idêntico** ao do Ticto.
2. Gere uma cobrança de **R$ 1,00** para um contato de teste e pague.
3. Na jornada do contato, confira: **um** evento, título legível, **produto** com o nome certo e
   **"Total gasto" +R$ 1,00**.
4. Clique "Conferir agora" no painel: **não** pode nascer um segundo evento (idempotência, R4).
5. Só então ligue a automação de liberação do curso.

---

## 8. Apêndice — arquivos-chave

**Plugin `pagamentos`** (fonte: `../../whatsbot-pro-plugins/plugins/pagamentos/src/`; cópia viva:
`../storages/plugins/pagamentos/` — **idênticas hoje**, verificado por `diff -rq`)

| Arquivo | Papel na mudança |
|---|---|
| `trackify_bridge.py` (102 linhas) | ⭐ F1 — a ponte inteira |
| `store.py` (419) | F2 (CRUD de produtos), F5 (`mark_refunded`, janela de reconsulta) |
| `routes.py` (591) | F2 (`/produtos`, `metadata`, `criar_cobranca`) |
| `reconcile.py` (163) | F5 (devolução) |
| `inter.py` (295) | F5 (só leitura de `devolucoes[]`; sem endpoint novo, salvo P3) |
| `notify.py` (380) | F5 (nota privada de estorno) |
| `static/CobrancaModal.js` | F3 (select de produto) |
| `static/config.js` | F4 (aba de produtos) |
| `migrations/003_produtos.sql`, `004_devolucao.sql` | novos |
| `plugin.yaml` | F8 (`version: 1.1.0`) |
| `tests/python/` | F0, F6 |

**Só leitura (contrato do outro lado)** — `../../whatsbot-pro-plugins/plugins/trackify/src/`:
`services.py:96-131` (`track_event`), `mirror.py:33-46` (kinds reservados), `:145-166` (`eligible`),
`:171-222` (`enqueue`), `journey.py:382-416` (`_product_identity`), `_config.py:91`
(`PRODUCT_IDENTITY_FIELDS`).

**Core (não muda):** `../plugins/services.py:296-321` (dispatch e isolamento),
`../plugins/migrator.py:39,106` (tradução do `AUTOINCREMENT`).

---

## 9. Checklist de verificação

- [ ] Teste de contrato da assinatura de `track_event` **verde** (o que teria pego este bug)
- [ ] `ServiceResult` com status inesperado gera **warning** — nunca mais falha muda
- [ ] `purchase` leva `valor`; `pix_gerado`/`pix_expirado` **não** levam
- [ ] Idempotência: webhook + varredura + "Conferir agora" ⇒ **um** evento no CDP
- [ ] `occurred_at` é o horário do pagamento, não o do processamento
- [ ] Migration 003/004: **sem `;` em comentário**; round-trip em banco novo **e** existente
- [ ] Prefixo `plugin_pagamentos_` em toda tabela/índice novo
- [ ] Cobrança **sem** produto continua funcionando (D7)
- [ ] Modal e aba de configuração legíveis no **modo escuro** (`wa-*`, `.wa-field`)
- [ ] Nenhuma opção nova no painel de Configurações do **core**
- [ ] `pix_code` fora do `data` do evento e fora da auditoria
- [ ] Sem `plugins.services` / trackify desligado / trackify ausente ⇒ o pagamento acontece igual
- [ ] `python3 scripts/test_plugins.py pagamentos` verde
- [ ] Suíte do core verde no Postgres (`WHATSBOT_TEST_DB_URL`)
- [ ] §7 aplicada na tela do Trackify **antes** do primeiro pagamento real
- [ ] "Total gasto" sobe no PIX pago e **volta** no estorno
- [ ] Versão instalada no dev conferida na interface **antes** de publicar o `.zip`
- [ ] Tabela `plugins` de produção consultada antes de afirmar paridade de versão
