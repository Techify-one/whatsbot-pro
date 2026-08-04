# Plano 100 — Devolver ao plugin o que é do plugin

**Status:** EM EXECUÇÃO — F0 segura concluída; F1 confirmado; a bancada externa do
plano 83 foi implantada; F2 GOWA continua bloqueado pelos gates de runtime. Escrito em
2026-07-31, atualizado em 2026-08-01.

> **Estado honesto desta tranche.** Foram removidos somente frontend morto, helpers órfãos e
> o catálogo enganoso de um filtro sem produtor. O gate assíncrono de `/protocolos` foi
> preservado por ser infraestrutura necessária de `overrideRoute`; nenhuma fonte de plugin
> em `assets/plugin_examples/` foi removida. Em paralelo, o plano 83 recebeu fundações de
> teste/empacotamento, mas publicação e extração GOWA continuam bloqueadas.

> **Atualização de 2026-08-01:** a fonte de desenvolvimento, os testes e os ZIPs dos 18
> plugins agora vivem juntos em `whatsbot-pro-plugins/plugins/<id>/{src,tests}`. O runner
> externo substitui a dependência de testes instalados e nenhum teste entra no artefato de
> produção. O GOWA publicado foi sincronizado com o bundled 1.3.1; isso **não** autoriza
> remover o GOWA do core, pois os gates de runtime desta F2 permanecem.

**Objetivo do usuário:** *"tudo que seja possível ir pro plugin vá somente pro plugin, com o
mínimo possível de coisa no core"* — diretriz dada na execução do plano 84, e a base da
**revisão geral de plugins** que o dono do projeto quer fazer ("o que eu posso retirar de
implementação que está no core e poderia ficar no plugin").

---

## 1. Este plano NÃO é o plano 83

Os dois falam de fronteira core↔plugin, mas em eixos diferentes:

| | **Plano 83** | **Plano 100 (este)** |
|---|---|---|
| Eixo | **Localização** — em que repositório o arquivo mora | **Responsabilidade** — qual lado implementa o comportamento |
| Pergunta | "esta pasta pode sair de `assets/plugin_examples/`?" | "este código deveria estar no core?" |
| Unidade | pasta de plugin | comportamento / gancho |
| Entregável | `.zip` publicado + pasta removida | linha a menos no core |
| Toca o plugin? | não muda comportamento de produção por objetivo; pode mover testes/metadados | pode reescrever os dois lados |

Os eixos são distintos, mas a implementação se encontra nas fundações de teste/distribuição,
na documentação e no bloco GOWA. Essa sobreposição operacional é justamente o motivo da
ordem abaixo.

### ⚠️ Ordem obrigatória: **o 100 vem ANTES do 83**

A evidência é o próprio plano 84, entregue esta semana. Os 5 arquivos que nasceram dele
(`alerts.py`, `filters.py`, `events.py`, `lifecycle.py`, `migrations/002_alert_state.sql`)
foram escritos **dentro de `assets/plugin_examples/whatsapp_cloud/`**, revisados por `git
diff` e exercitados pela suíte do core. A release 1.10.1 foi empacotada/publicada nesse
fluxo; a revisão posterior levou fonte/instalação e artefato publicado a 1.10.2.

A pasta no repo do core é a **bancada e o banco de testes** de todo movimento core→plugin.
O plano 83 remove exatamente essa bancada. Na ordem invertida, cada extração passaria a
exigir editar um `.zip` ou uma cópia gitignorada em `storages/`, **sem diff revisável e sem
teste** — que é a classe de bug que o próprio plano 83 usa como argumento central (o split
`assets`↔`storages` que escondeu 48% da suíte por semanas).

---

## 2. A regra de decisão (produzida na execução do plano 84)

Um comportamento só merece **ramo, evento ou campo novo no core** quando os **três** forem
verdade ao mesmo tempo:

1. **≥ 2 consumidores previstos** — consumidores reais, não hipotéticos;
2. **nenhum gancho existente enxerga o sinal**;
3. **usar o gancho existente custaria caro no caminho quente**.

Falhando **qualquer um** dos três, o comportamento de negócio vai inteiro para o plugin.
**Exceção explícita:** procedência, autenticação e autorização verificáveis na borda são
seams de fronteira de confiança do core, mesmo quando só um plugin consome o resultado.

Como o plano 84 mediu cada um, ao decidir entre `kind="account"` no core (opção *a*, a
recomendação escrita) e `filter.webhook.payload` dentro do plugin (opção *b*, a executada):

| Critério | Medição | Veredito |
|---|---|---|
| (i) ≥2 consumidores | nenhum outro plugin consumiria `channel.account_event` | falha |
| (ii) sem gancho | `filter.webhook.payload` já enxergava — e `janela_72h` + `debug_bus` **já o usavam em produção** pelo mesmo motivo | falha |
| (iii) custo no hot path | 0,32 µs por payload GOWA; 0,73 µs por inbound normal da Meta | falha |

Três falhas ⇒ a **classificação e o alerta de conta** ficaram no plugin; não nasceu
`kind="account"`, ramo de dispatch nem estado de alerta no core. A execução final do plano
84, porém, adicionou ao core o seam genérico de **procedência e autenticação**
(`provider`, `channel_id`, `signature_authenticated`). Isso pertence à fronteira de confiança
da rota, não ao comportamento de negócio do plugin. A formulação antiga “o core não recebeu
uma linha” estava desatualizada.

> **Precedente é evidência.** Quando dois plugins já resolveram o mesmo problema pelo mesmo
> gancho em produção, o gancho está provado — o ônus da prova passa para quem quer o ramo no
> core, não para quem quer o gancho.

### 2.1 O ganho real não é estético: a dependência de deploy fica explícita

A execução final do plano 84 **manteve** a ordem “core antes do zip” para a fonte webhook:
em core antigo, `ctx.extras` não prova procedência/assinatura e o plugin degrada fechado, sem
enviar esse alerta. As outras fontes (`message.failed` e polling) continuam funcionando. O
ganho correto foi restringir a mudança do core a um seam de confiança reutilizável, em vez
de ensinar o core sobre eventos de conta da Meta.

Critério de aceite herdável: **o plugin deve ao menos carregar num core da release anterior,
e toda feature que dependa de seam novo deve degradar de forma explícita e segura.** Quando a
feature exige o seam, a ordem de deploy precisa constar no release/deploy; não se promete
autonomia que os testes não demonstram.

### 2.2 "Pouco core" não significa "sem dependência do core"

O plugin do plano 84 importa o core em 4 pontos:
`db.repositories`, `plugins.context`, `runtime.supervisor`, `server.message_errors`.

O que torna isso seguro num plugin distribuído por zip é o **import defensivo**: cada
dependência opcional está em `try/except` que degrada em vez de quebrar (`describe_failure`
indisponível cai no `error_title`; supervisor ausente só desliga o polling e loga;
`spawn_task` não cabeado é `RuntimeError` tratado).

**Regra:** todo import do core além do mínimo é defensivo. Sem isso, um core mais velho vira
erro de import e o plugin **nem carrega** — falha muda, no boot, sem tela.

---

## 3. O que foi medido (auditoria de 2026-07-31)

**Método:** 2 varreduras independentes (backend, frontend) → 16 candidatos → **um refutador
adversarial por candidato**, com viés padrão `refutado=true` e obrigação de conferir
arquivo:linha, consumidores reais e existência do gancho proposto → crítico de completude.

**Resultado: 3 sobreviveram, 13 caíram.**

> ⚠️ **A lista dos 13 refutados é a parte mais valiosa deste documento.** Ela é o *"não
> refaça isto"* da revisão geral — cada um parece um acoplamento e não é.

---

## 4. Falsos positivos — parecem acoplamento, não são (não mexer)

| # | Candidato | Por que NÃO se move |
|---|---|---|
| 1 | Subsistema de templates HSM no core (`template_service.py`, 377 linhas + 11 rotas) | Já é **genérico por capability** (`outbound.supports(channel_id,"templates")`), sem `if provider ==`. O vocabulário da Meta já saiu para `TemplateSpec` no descriptor. **O plano 92 avaliou e rejeitou isto por escrito, duas vezes** |
| 2 | `TemplatePicker.js` congelado no core (845 linhas) | Não sobra o que extrair: o modal real **já é do plugin** (plano 92 · B1). O que resta é fallback de transição declarado, com data para morrer |
| 3 | Códigos de erro da Meta em `server/message_errors.py` | Só as linhas 30-48 são vocabulário da Meta (13 códigos); o resto é política genérica que **tem de ficar**. O alvo real é ~19 linhas, não 106 |
| 4 | `channels.gowa_device_id` / `gowa_isolation` no schema do core | O gancho proposto (`hasattr`) **nunca dispara** — `Channel.reconnect/logout` existem na base devolvendo `{"ok": False}`. A troca quebraria teste |
| 5 | "Janela de 24h" escrita à mão na UI | O backend serializa só booleanos; o número no texto é **cópia**, não fonte. Trocar por dado do descriptor é melhoria de UI, não extração |
| 6 | `CONTACT_TYPE_META` (rótulo/cor de telegram/facebook/instagram) | Premissa falsa: **não** são as únicas ocorrências (contraexemplo medido em `NewConversationModal.js:123`). É base curada + descoberta pelo catálogo, por desenho |
| 7 | `gowa_status` no barramento de eventos | Nome de plugin cravado, verdade — mas as duas premissas da extração são falsas quando medidas |
| 8 | Onboarding/saldo da Techify no core | Mistura fornecedor de LLM com encanamento genérico; e o gancho proposto não existe |
| 9 | `SetupWizard` passo 1 (QR do GOWA) | A saída preferida troca um arquivo do core por outro arquivo do core: é deduplicação interna, **não extração**. O slot `wizard.steps` não existe |
| 10 | Tombstone do `gowa` + rotas SPA `/protocolos` | Existe (`plugins.py:261`, `app.py:542/700-702`) e é dívida real, mas cada um tem motivo estrutural distinto — ver §5.C1 |
| 11 | `if provider == "gowa"` no inbound | Citações corretas, premissa central errada — **são 7 sites, não 3**, e três deles nenhum candidato listou (`channels/registry.py:108`, `server/app.py:185`, `message_ingest_service.py:364`) |
| 12 | Provider GOWA inteiro (2.426 linhas, incluindo `gowa/__init__.py`) | **Diagnóstico verdadeiro, extração como descrita refutada**: "todos os ganchos já existem" é falso. Vira o §6 |
| 13 | Falta seam de render de mídia no painel | A premissa (`filter.media.unknown` deixa um plugin criar tipo novo) é **falsa**: o gancho está morto — ver §7.2 |

---

## 5. Os 3 candidatos que sobreviveram

### C1 — `web/static/js/components/attendances/` — 932 linhas mortas no core

**Não é extração: é deleção.** O plugin `protocolos` já reimplementou a tela inteira do zero
(130 KB) e a serve por `overrideRoute('attendances')`. O código do core **já não é
alcançável**.

Provado por 4 vias independentes: nenhum `import` do diretório em todo o `web/`; nenhum
import dinâmico; as únicas menções fora dele eram comentários no próprio plugin; e o
renderer só podia ser alcançado dentro da árvore isolada. A tranche apagou os oito arquivos
e corrigiu o comentário residual do plugin.

Efeito colateral medido antes da deleção: o slot `attendances.toolbar` tinha seu **único**
render site no `Attendances.js` hoje removido — por isso sua documentação também pôde sair.

| Decisão | Risco |
|---|---|
| (a) apagar os 8 arquivos + a doc do slot, preservando o gate de `ScreenRouter.js` | **executado** — remove a tela nativa sem quebrar o carregamento assíncrono do override |
| (b) tirar `/protocolos` do roteador do core (fazer o `opts` de `overrideRoute` carregar o path) | **não é grátis** — quebra `routing.test.js` e o hardcode em `server/app.py:542` + `:700-702` é o que faz um reload duro em `/protocolos` ser servido |

✅ `getConversationLabelsBatch` saiu de `services/api.js` e da superfície 2.x (o adapter 1.x
o preserva durante a transição); o slot `attendances.toolbar`, sem render site, foi removido
de fato. As outras funções correlatas permaneceram.

> **Correção importante da auditoria:** o ramo `tab === 'attendances'` de `ScreenRouter` não
> era parte da tela morta. Ele espera `extensionsLoaded` para impedir que um F5 em
> `/protocolos` renderize fallback/redirecione antes de `extends.js` registrar
> `overrideRoute('attendances')`; depois do load, sem plugin, volta para Contatos. O ramo e o
> estado em `App.js` foram preservados e tiveram os comentários generalizados.

### C2 — `getGowaAlertSettings` — removido da superfície atual; adapter legado isolado

`web/static/js/services/api.js:94-99` — `GET /api/plugins/gowa/alert-settings`. É o **único**
helper do core que nomeia um plugin numa URL, e viola a regra que o plano 76 registrou 700
linhas abaixo no mesmo arquivo (*"o core não chama mais endpoint de plugin daqui"*).

Consumidores: **zero**, em qualquer forma — nos 14 plugins instalados, nos 16 do repo do Pro,
por acesso dinâmico. O comentário acima da função documenta um uso que **não existe mais**
(o consumidor real morreu no refactor descriptor-driven). O papel dele migrou para o backend
do plugin (`gowa/alerts.py:369-374`) e o toggle virou `config_field` do descriptor.

O agravante histórico era ele ser exportado a todos os plugins via `api.services`. A
execução foi deliberadamente em duas camadas:

- ✅ o helper saiu de `web/static/js/services/api.js` e da superfície atual 2.x;
- ✅ `PLUGIN_SERVICES_VERSION` subiu de 1.0 para 2.0;
- ✅ `plugin_services_version` passou a ser publicado no manifest e verificado pelo loader;
  range inválido/incompatível pula o `frontend_extends` (fail-closed);
- ✅ manifests antigos/ausentes negociam 1.x e recebem um adapter estreito em
  `plugins/api.js`, que preserva os dois helpers removidos por chamadas HTTP genéricas.

Assim, o core corrente não oferece mais o helper na API normal nem na superfície 2.x, mas
uma extensão externa legada não quebra silenciosamente durante a transição. Plugins novos
devem declarar `plugin_services_version: ">=2.0,<3.0"` e ainda fazer feature detection.

### C3 — `agent/group_mentions.py` — 439 linhas de WhatsApp puro no core

**Sobreviveu na substância, com o escopo corrigido para maior.** O serviço é modelado sobre o
fio do WhatsApp (`@<número>` ↔ `@<Nome>`, `lid`, `@everyone`), só funciona com o `GOWAClient`
injetado (`server/app.py:303`), **não passa pelo contrato `Channel` nem pelo `OutboundRouter`**
— e chega a chamar um **método privado de outro módulo** (`_client._get_user_info`, definido
em `gowa/client.py:799`).

Correções que a refutação impôs ao candidato original:

- `resolve_incoming` **não precisa de gancho**: os únicos chamadores de produção já estão em
  `gowa/inbound.py` (o parser GOWA) e descem junto com ele. O gancho real é só para
  `resolve_outgoing`.
- O **transporte** de menções já existe no contrato (`Channel.send_text(..., mentions=None)`,
  repassado pelo `OutboundRouter`). Falta só o hook de **resolução**.
- O maior consumidor foi omitido: `gowa/inbound.py` (5 chamadas). São 8 arquivos de produção.
- Faltam 2 ganchos que não existem: `can_send_in_chat` e `delete_for_me`
  (hoje `contacts.py:1170` é `if channel_id == "default"` — um `if provider ==` disfarçado).

**Bug já ativo hoje, que a extração mata por construção:** o Telegram declara `groups=True`,
então o guard por capability passa e o **core chama `gowa_client.can_bot_send_in_group()` com
um `chat_id` do Telegram** (`contacts.py:872-876` — o comentário na linha 869 afirma
literalmente o contrário do que o código faz). São **dois** caminhos, não um: o segundo é
`prompt_builder.py:75`, que gateia só por capability, sem guard de `@g.us`.

> **Veredito:** C3 **não é uma extração autônoma**. É uma fatia da mudança do §6 e só deve ser
> feita no mesmo lote. Risco **alto**.

⏳ **Estado:** não executado nesta tranche. Nenhum arquivo de `agent/group_mentions.py`,
`gowa/`, `channels/providers/gowa_channel.py` ou das rotas/background GOWA foi movido.

---

## 6. A área que ninguém varreu: o pacote `gowa/` no core

`gowa/` tem **1.791 linhas** (`client.py` 873, `inbound.py` 736, `manager.py` 180) de código
exclusivamente GOWA **no core** — e o **plugin `gowa` importa de volta desse pacote**
(`assets/plugin_examples/gowa/{lifecycle,processes}.py`). O `gowa/inbound.py` documenta na
própria docstring ser *"o equivalente GOWA do `WhatsAppCloudChannel.parse_inbound`"*.

Somado ao resto do provider (`channels/providers/gowa_channel.py` 434, `routes/whatsapp.py`
78 e 123 linhas de polling em `server/background.py`), são **2.426 linhas** contando o
`gowa/__init__.py` de 2 linhas (**2.424** sem ele), além de **29 arquivos `.py` do core** que
citam `gowa`.

É o **maior bloco de código de provider no core** e o **único caso em que um plugin importa
do core a própria implementação específica do provider** (`gowa/`) — nesse recorte, a
relação está invertida. Os demais plugins também importam muitos módulos internos genéricos,
dívida separada medida em R1.

O texto original do plano 83 dizia manter `gowa` como o único plugin bundled; isso não deve
ser confundido com manter sua implementação de provider no core para sempre. A F2 pode
devolver essa implementação ao próprio plugin, mas só depois dos contratos abaixo e sem
antecipar os gates de empacotamento/publicação do 83. É aqui que a revisão geral tem o maior
retorno, e é o pré-requisito de C3.

⚠️ Não é trivial: `create_app(settings, gowa_manager, gowa_client, agent_handler)` recebe os
dois objetos como **parâmetros posicionais obrigatórios**, e `main.py` + `server/dev.py` os
constroem **incondicionalmente**. O plugin `gowa` já declara essa mudança como *"deferred
follow-up"* (plano 13 §2.1).

---

## 7. Os ganchos que faltam (o que hoje impede o zero-core)

### 7.1 A costura de proveniência já existia — confirmada nesta tranche

O documento estava desatualizado: `server/routes/channel_webhook.py:734-741` já aplica o
filtro depois da resolução de rota/provider e da verificação de assinatura:

```python
raw = await apply_filter("filter.webhook.payload", raw, {
    "provider": provider,
    "channel_id": channel_id,
    "signature_authenticated": signature_authenticated,
})
```

Os testes de integração do plano 84 já cobrem o loader/endpoint real e os casos negativos de
assinatura/provider/WABA. Portanto a F1 não exigiu mudança nesta tranche; foi
**confirmada como previamente entregue**, inclusive com um sinal mais forte que o proposto
originalmente.

Não existe cascata permissiva nesse consumidor: o plugin exige simultaneamente provider
Cloud, canal ativo exato, `signature_authenticated=True` e `entry[].id == waba_id` daquele
canal. Num core anterior, sem esses extras, a captura webhook degrada **fechada**; polling e
`message.failed` seguem disponíveis. Essa é uma dependência de deploy intencional e testada,
não compatibilidade total com core antigo.

> **Regra generalizável:** toda credencial que identifica a conta (`waba_id`, `page_id`,
> `phone_number_id`, `bot_id`) é **também** a chave de auto-resolução do plugin — o mesmo
> namespace que o contrato `AccountIdentity` do plano 32 já usa.

### 7.2 `filter.media.unknown` foi enterrado explicitamente

✅ O nome legado foi removido de `KNOWN_FILTERS` e da documentação de criação de plugin. O
CLAUDE.md agora registra que mídia deve ser normalizada pelo provider em
`Channel.parse_inbound()` para um `InboundEvent.kind` suportado. Um plugin que ainda tente
registrar `filter.media.unknown` recebe WARNING de filtro desconhecido, coberto por teste de
regressão, em vez de aceitar silenciosamente um gancho que nunca seria chamado.

### 7.3 Inventário do que não tem gancho

| Área | Situação |
|---|---|
| **Dispatch de inbound** | cadeia fechada de 12 `kind` literais, **sem `else`**; `kind` desconhecido cai fora sem log. Não há gancho entre o parse e o dispatch |
| **Envio de mídia do operador** | as 4 rotas (`/send-image`, `/send-audio`, `/send-document`, `/send-video`) **não aplicam filtro nenhum**. Um plugin não bloqueia, transforma nem carimba anexo de saída — só observa depois, por `message.sent` |
| **Broadcast WebSocket** | `ConnectionManager.broadcast` faz fan-out sem filtro e sem escopo. O único seam de projeção é declaradamente core-only |
| **Dimensões de filtro** | `db/filters/registry.py` expõe `DIMENSIONS`/`OPS` como dicts **estáticos**, sem função de registro |
| **Gate da IA / criação de contato** | `ai_may_speak`, `_resolve_ai_seed` e a materialização do contato não passam por filtro. Um plugin não decide "esta conversa nasce com IA off" nem enriquece o contato no INSERT |
| **Batching do inbound** | o acúmulo e a junção dos textos acontecem sem filtro |
| **Frontend: render de mensagem** | não há slot nem filtro para o corpo de uma bolha/card — os roles painel-only são `if` dentro do `ContactDetail.js`. Também não há slot no compositor |

Os **8 slots** que permanecem: `channel.card.rows`, `sidebar.row.badges`,
`ai.settings.sections`, `conversation.info.panel`,
`chat.header.banner`, `gear.menu.items`, `app.overlay`, `conversation.header.actions`.

⚠️ `EventContext` **não tem** campo `extras` — só `FilterContext` tem. Um handler de evento
nunca recebe contexto do call site.

---

## 8. O contrato do observador de webhook (5 armadilhas)

O mesmo texto de aviso já aparece **palavra por palavra em três plugins** (`janela_72h`,
`debug_bus`, `whatsapp_cloud`). Três cópias é sinal de contrato que devia estar documentado,
não folclore copiado:

1. **Devolver `None` DESCARTA a mensagem inbound.** O core responde 200 sem processar. Um
   observador que erre isso derruba a caixa de entrada inteira.
2. Exceção do filtro é isolada pelo core (loga e o valor segue) — **mesmo assim o observador
   engole tudo por conta própria**.
3. **Prioridade: número MENOR roda ANTES.** Observador usa 9000 para rodar por último e nunca
   disputar com filtro que de fato transforma.
4. O filtro roda para **todos** os providers em **todo** inbound (call site único; o GOWA está
   100% nessa rota) — o guard precisa sair na **primeira comparação**.
5. Trabalho pesado (banco/rede) é **offloaded** para fora do request via `loop.create_task`,
   guardando **referência forte** da task (o loop só guarda fraca) e tratando "sem loop".

---

## 9. Riscos

### R1 — A superfície de import Python não é versionada (o risco maior)

Os plugins de `assets/` fazem **100+ imports de módulos internos do core** (45 de
`db.repositories`, 30 de `plugins.context`, 14 de `channels.base`, mais `server.routes.*`,
`server.background`, `server.authz`, `ai_engine`, `app.services`, `gowa.manager`,
`runtime.supervisor`, `db.tables`, `agent`) — **nenhum deles é API declarada**.

E **os 22 manifests/cópias não ocultos medidos** (15 ids únicos) declaram o mesmo
`whatsbot_api_version: ">=1.0,<2.0"` contra
`WHATSBOT_API_VERSION = "1.0.0"`, que **nunca foi bumpada** desde a criação do sistema de
plugins. O guard nunca rejeitou nada e, como está, nunca vai rejeitar.

Hoje o **único** detector de "refactor do core quebrou o plugin" é a fonte do plugin estar no
repo e a suíte exercitá-la. Num repo **sem CI** (não existe `.github/`) e sem check de versão
remoto, esse detector é tudo o que há.

**Atualização:** o plano 83 já entregou o primeiro substituto: cada plugin mantém os testes
ao lado de `src/`, e `scripts/test_plugins.py` os executa contra um checkout explícito do
core; `scripts/build_plugins.py --check --all` prova que os ZIPs correspondem à fonte e não
contêm testes. Ainda falta CI e continua válido versionar a superfície Python pública.

### R2 — Duplicação entre plugins é o preço, e é aceito

O precedente está registrado: quando o Instagram entrou, ele carregou a **própria cópia** de
`MetaGraphChannel` em vez de importar a do `facebook_messenger` (plano 76 · D2/F9) —
*"dois canais Meta, duas cópias — preço do zip autossuficiente"*. Um plugin **não importa de
outro plugin**. Quem propuser fatorar código comum entre plugins está reintroduzindo o
acoplamento por outro caminho.

### R3 — Fora do repo, ninguém revisa o ramo morto

Regra herdada do 84: o parse/classificação do plugin degrada para **bruto e visível**, nunca
para silêncio (campo não catalogado vira grupo `unknown` com o JSON resumido; config
desconhecida é tratada como ligada). Fora do repo do core, ninguém vai notar um branch morto
no próximo code review.

---

## 10. Fases sugeridas

### F0 — ✅ Tranche segura concluída
1. ✅ Apagados os oito arquivos de `web/static/js/components/attendances/`, a doc do slot
   morto e `getConversationLabelsBatch`.
2. ✅ Removido `getGowaAlertSettings` da API normal e da superfície 2.x, com adapter apenas
   na compatibilidade 1.x; `plugin_services_version` agora é negociado pelo manifest/loader.
3. ✅ `filter.media.unknown` enterrado no catálogo e na documentação, com teste de regressão.
4. ✅ Preservados `extensionsLoaded` e o ramo de `ScreenRouter` que aguardam o
   `overrideRoute`; a decisão (b), os paths SPA e o reload duro de `/protocolos` ficaram fora.

**Limite do gate:** esta conclusão cobre a limpeza segura. Não autoriza F2 nem remoção de
fontes de plugin; esses passos dependem dos gates descritos abaixo e no plano 83.

### F1 — ✅ Já existia; confirmado
O filtro já recebe `provider`, `channel_id` e `signature_authenticated` (§7.1). O consumidor
Cloud é fail-closed e requer esse seam; em core antigo, apenas suas outras fontes de alerta
continuam funcionando.

### F2 — ⏳ O bloco GOWA (§6 + C3) — BLOQUEADO
Único lote grande. Antes de mover código, o core precisa de construção genérica de provider,
`create_app` com `gowa_manager`/`gowa_client` opcionais, contratos de grupos/menções/membros,
`can_send_in_chat`/`delete_for_me`, resolução de device/webhook e separação entre tarefas
GOWA e tarefas genéricas de background. Também faltam boot real sem GOWA e regressões
cross-provider. As fundações de teste do plano 83 ajudam, mas não fecham esses gates.

**Não remover GOWA do core ainda:** o artefato externo 1.3.1 está sincronizado e pode ser
usado para atualizar instalações, mas a implementação ainda reexporta componentes do core e
o fresh install depende do bundled. Extração física continua bloqueada pelos gates acima.

### F3 — A revisão geral, plugin a plugin
Com o checklist do §11.

---

## 11. Checklist da revisão geral (um plugin de cada vez)

Para cada plugin instalado, responder:

- [ ] **Zero-core?** O plugin funciona num core da release anterior? Se não, o que ele exige do
      core e por quê?
- [ ] **Imports defensivos?** Todo import além do mínimo está em `try/except` que degrada?
- [ ] **O core sabe o nome dele?** `grep` do id do plugin fora de `plugins/bootstrap.py` — rota
      SPA, tombstone, helper de API, evento nomeado.
- [ ] **O core carrega vocabulário da plataforma dele?** Constantes, limites, códigos de erro,
      categorias — o provider **declara**, o core **avalia**.
- [ ] **Estado sobrevive ao restart?** Tabela `plugin_<id>_*`, nunca memória (o toggle de plugin
      derruba o processo).
- [ ] **Tem teste de costura?** Ao menos **um** teste que sobe o app pelo **loader real** e bate
      no **endpoint real**. Teste que carrega o módulo por caminho continua verde com a costura
      arrancada — não substitui.
- [ ] **Degrada para visível, nunca para silêncio?**
- [ ] **Se roda sozinho:** `ctx.spawn_task` + `RestartPolicy.PERMANENT`, agregação e cooldown
      **desde o 1º dia**, read-modify-write serializado por `asyncio.Lock`.

---

## 12. Perguntas em aberto

**P1 — A linha do `extras` (§7.1) entra ou não? — RESOLVIDA**
Já existia com `provider`, `channel_id` e `signature_authenticated`; nenhuma mudança foi
necessária nesta tranche. O consumidor Cloud exige esse contexto e degrada fechado em core
antigo; não existe cascata permissiva.

**P2 — `filter.media.unknown`: reviver ou enterrar? — RESOLVIDA**
Enterrado explicitamente; providers normalizam para kinds suportados em `parse_inbound`.

**P3 — A decisão (b) do C1 (tirar `/protocolos` do roteador do core)? — ADIADA**
O hardcode SPA e o gate assíncrono foram preservados; a F0 removeu apenas a implementação
nativa morta.

**P4 — Versionar a superfície de import (R1) agora ou junto do 83?**
O frontend já ganhou um gate separado para `api.services`, mas isso não versiona os 100+
imports Python internos. O P4 continua pré-requisito do 83: **F1–F7 não devem remover fonte**
sem uma das duas saídas (contract test contra os zips, ou superfície Python pública
versionada).
