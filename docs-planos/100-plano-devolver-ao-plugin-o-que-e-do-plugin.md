# Plano 100 — Devolver ao plugin o que é do plugin

**Status:** PLANEJADO — nada executado. Escrito em 2026-07-31.

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
| Toca o plugin? | não muda uma linha de código | reescreve os dois lados |

Não há sobreposição de arquivos: nenhum dos candidatos deste plano é citado no 83.

### ⚠️ Ordem obrigatória: **o 100 vem ANTES do 83**

A evidência é o próprio plano 84, entregue esta semana. Os 5 arquivos que nasceram dele
(`alerts.py`, `filters.py`, `events.py`, `lifecycle.py`, `migrations/002_alert_state.sql`)
foram escritos **dentro de `assets/plugin_examples/whatsapp_cloud/`**, revisados por `git
diff`, exercitados pela suíte do core e só então empacotados para o zip publicado.

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

Falhando **qualquer um** dos três, o comportamento vai inteiro para o plugin.

Como o plano 84 mediu cada um, ao decidir entre `kind="account"` no core (opção *a*, a
recomendação escrita) e `filter.webhook.payload` dentro do plugin (opção *b*, a executada):

| Critério | Medição | Veredito |
|---|---|---|
| (i) ≥2 consumidores | nenhum outro plugin consumiria `channel.account_event` | falha |
| (ii) sem gancho | `filter.webhook.payload` já enxergava — e `janela_72h` + `debug_bus` **já o usavam em produção** pelo mesmo motivo | falha |
| (iii) custo no hot path | 0,32 µs por payload GOWA; 0,73 µs por inbound normal da Meta | falha |

Três falhas ⇒ plugin. O core não recebeu uma linha.

> **Precedente é evidência.** Quando dois plugins já resolveram o mesmo problema pelo mesmo
> gancho em produção, o gancho está provado — o ônus da prova passa para quem quer o ramo no
> core, não para quem quer o gancho.

### 2.1 O ganho real não é estético: é ordem de deploy

A F8 original do plano 84 exigia **"core antes do zip"** — zip novo rodando em core velho
descartaria os eventos em silêncio. Com a reimplementação zero-core esse item foi **riscado**
do checklist e virou *"basta importar o zip"*.

Critério de aceite herdável por qualquer plugin: **o plugin instala e funciona num core da
release anterior.** Verificável, e é exatamente a premissa de que o plano 83 depende para o
`.zip` virar unidade de deploy autônoma.

### 2.2 "Não muda o core" ≠ "não depende do core"

O plano 84 não mudou uma linha do core **e mesmo assim importa o core em 4 pontos**:
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
| 12 | Provider GOWA inteiro (2.301 linhas) | **Diagnóstico verdadeiro, extração como descrita refutada**: "todos os ganchos já existem" é falso. Vira o §6 |
| 13 | Falta seam de render de mídia no painel | A premissa (`filter.media.unknown` deixa um plugin criar tipo novo) é **falsa**: o gancho está morto — ver §7.2 |

---

## 5. Os 3 candidatos que sobreviveram

### C1 — `web/static/js/components/attendances/` — 932 linhas mortas no core

**Não é extração: é deleção.** O plugin `protocolos` já reimplementou a tela inteira do zero
(130 KB) e a serve por `overrideRoute('attendances')`. O código do core **já não é
alcançável**.

Provado por 4 vias independentes: nenhum `import` do diretório em todo o `web/`; nenhum
import dinâmico; as únicas menções fora dele são **2 comentários** no próprio plugin; e o
ramo em `ScreenRouter.js:159-171` retorna `null` + `setTab('contacts')` quando não há
override. `git status` limpo nesses arquivos — o estado morto é o commitado.

Efeito colateral: o slot `attendances.toolbar` tem seu **único** render site em
`Attendances.js:386` — o slot também está morto.

| Decisão | Risco |
|---|---|
| (a) apagar os 8 arquivos + o ramo `ScreenRouter.js:159-171` + a doc do slot | **zero** — nenhum teste quebra |
| (b) tirar `/protocolos` do roteador do core (fazer o `opts` de `overrideRoute` carregar o path) | **não é grátis** — quebra `routing.test.js` e o hardcode em `server/app.py:542` + `:700-702` é o que faz um reload duro em `/protocolos` ser servido |

⚠️ Cascata: `getConversationLabelsBatch` (`services/api.js:642`) perde o único consumidor
com a remoção. As outras 9 funções correlatas **não** ficam órfãs.

### C2 — `getGowaAlertSettings` — 6 linhas, o core chamando endpoint de plugin

`web/static/js/services/api.js:94-99` — `GET /api/plugins/gowa/alert-settings`. É o **único**
helper do core que nomeia um plugin numa URL, e viola a regra que o plano 76 registrou 700
linhas abaixo no mesmo arquivo (*"o core não chama mais endpoint de plugin daqui"*).

Consumidores: **zero**, em qualquer forma — nos 14 plugins instalados, nos 16 do repo do Pro,
por acesso dinâmico. O comentário acima da função documenta um uso que **não existe mais**
(o consumidor real morreu no refactor descriptor-driven). O papel dele migrou para o backend
do plugin (`gowa/alerts.py:369-374`) e o toggle virou `config_field` do descriptor.

Agravante: por não estar em `PLUGIN_SERVICES_DENY`, a função é exportada **a todos os
plugins** via `api.services`. Apagar o export a remove da superfície automaticamente.

### C3 — `agent/group_mentions.py` — 432 linhas de WhatsApp puro no core

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

---

## 6. A área que ninguém varreu: o pacote `gowa/` no core

`gowa/` tem **1.791 linhas** (`client.py` 873, `inbound.py` 736, `manager.py` 180) de código
exclusivamente GOWA **no core** — e o **plugin `gowa` importa de volta desse pacote**
(`assets/plugin_examples/gowa/{lifecycle,processes}.py`). O `gowa/inbound.py` documenta na
própria docstring ser *"o equivalente GOWA do `WhatsAppCloudChannel.parse_inbound`"*.

Somado ao resto do provider (`channels/providers/gowa_channel.py` 434, `routes/whatsapp.py`
78, os 123 linhas de polling em `server/background.py`), são **2.301 linhas** e **29 arquivos
`.py` do core** que citam `gowa`.

É o **maior bloco de código de provider no core** e o **único caso em que o plugin depende do
core por import direto de um pacote que não é contrato** — a relação está invertida em
relação a todos os outros providers.

O plano 83 mantém o `gowa` no core, então isto não o bloqueia. Mas é aqui que a revisão geral
tem o maior retorno, e é o pré-requisito de C3.

⚠️ Não é trivial: `create_app(settings, gowa_manager, gowa_client, agent_handler)` recebe os
dois objetos como **parâmetros posicionais obrigatórios**, e `main.py` + `server/dev.py` os
constroem **incondicionalmente**. O plugin `gowa` já declara essa mudança como *"deferred
follow-up"* (plano 13 §2.1).

---

## 7. Os ganchos que faltam (o que hoje impede o zero-core)

### 7.1 A única linha do core que barateia todos os plugins de canal de uma vez

`server/routes/channel_webhook.py:673` passa `{}` como extras:

```python
raw = await apply_filter("filter.webhook.payload", raw, {})
```

Não é "ainda não preenchido" — o core passa literalmente vazio, e o construtor do contexto só
atribui `extras` quando o dict é truthy. **Consequência:** nenhum plugin que use este gancho
sabe qual canal recebeu o payload, nem mesmo para o GOWA, cujo `channel_id` está na URL.

Passar `{"provider": provider, "channel_id": channel_id}` é **uma linha** e beneficia todos os
plugins de canal extraídos. É a única mudança de core que este plano recomenda sem reservas.

Enquanto ela não existe, o padrão é a **cascata de auto-resolução em 3 níveis do plano 84**,
que nunca degrada para silêncio:

1. casar o identificador do payload (`entry[].id` = WABA id) contra a credencial declarada do
   canal (`waba_id`);
2. não casando, se existir **exatamente um** canal habilitado daquele provider, é ele;
3. sem nada disso, seguir mesmo assim com o identificador no texto — *melhor um alerta sem
   etiqueta do que silêncio*.

> **Regra generalizável:** toda credencial que identifica a conta (`waba_id`, `page_id`,
> `phone_number_id`, `bot_id`) é **também** a chave de auto-resolução do plugin — o mesmo
> namespace que o contrato `AccountIdentity` do plano 32 já usa.

### 7.2 `filter.media.unknown` está MORTO

Declarado em `KNOWN_FILTERS` (`plugins/events.py:139`) e documentado no CLAUDE.md e no
`/new-plugin` como gancho vivo — **mas não existe nenhum `apply_filter("filter.media.unknown",
…)` no repositório**. O call site existiu (commit `755cb1a`) e sumiu no refactor do webhook.

Um plugin que o registre **nunca é chamado, e nem leva WARNING**, porque o nome está no
catálogo. Hoje **não há como um plugin reivindicar um media type novo**.

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

Os **9 slots** que existem hoje: `channel.card.rows`, `sidebar.row.badges`,
`ai.settings.sections`, `attendances.toolbar` (morto — C1), `conversation.info.panel`,
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

### R1 — A superfície de import não é versionada (o risco maior)

Os plugins de `assets/` fazem **100+ imports de módulos internos do core** (45 de
`db.repositories`, 30 de `plugins.context`, 14 de `channels.base`, mais `server.routes.*`,
`server.background`, `server.authz`, `ai_engine`, `app.services`, `gowa.manager`,
`runtime.supervisor`, `db.tables`, `agent`) — **nenhum deles é API declarada**.

E **todos os 22 plugins** declaram o mesmo `whatsbot_api_version: ">=1.0,<2.0"` contra
`WHATSBOT_API_VERSION = "1.0.0"`, que **nunca foi bumpada** desde a criação do sistema de
plugins. O guard nunca rejeitou nada e, como está, nunca vai rejeitar.

Hoje o **único** detector de "refactor do core quebrou o plugin" é a fonte do plugin estar no
repo e a suíte exercitá-la. Num repo **sem CI** (não existe `.github/`) e sem check de versão
remoto, esse detector é tudo o que há.

**Consequência para os dois planos:** o plano 83 remove o detector sem substituto. Ou a suíte
passa a rodar contra os **zips publicados** (contract test), ou se define e versiona uma
superfície pública (`plugins.context` + `channels.base`) e se bumpa a versão de verdade.

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

### F0 — Grátis, sem risco, faz sozinho (fazer já)
1. Apagar `web/static/js/components/attendances/` (932 linhas) + o ramo `ScreenRouter.js:159-171`
   + a doc do slot morto + `getConversationLabelsBatch`.
2. Apagar `getGowaAlertSettings` e o comentário que documenta o uso inexistente.
3. Corrigir a documentação de `filter.media.unknown` (§7.2) — ou revivê-lo, ou marcá-lo como
   morto. Hoje ela mente para quem for escrever plugin.

**Gate:** suíte verde; `routing.test.js` intacto (a decisão (b) do C1 fica de fora da F0).

### F1 — A linha que barateia tudo
Passar `{"provider", "channel_id"}` no `filter.webhook.payload` (§7.1). Manter a cascata de
auto-resolução nos plugins — ela continua sendo o caminho para quem roda em core antigo.

### F2 — O bloco GOWA (§6 + C3)
Único lote grande. Pré-requisito: `create_app` aceitar `gowa_manager`/`gowa_client` opcionais.
Ganho colateral: mata os 2 caminhos do bug Telegram→GOWA.

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

**P1 — A linha do `extras` (§7.1) entra ou não?**
É a única mudança de core que este plano propõe. Contra: o 84 provou que dá para viver sem
ela. A favor: barateia **todos** os plugins de canal de uma vez e não muda comportamento
nenhum. **Recomendação: entra**, na F1, e os plugins mantêm a cascata como fallback.

**P2 — `filter.media.unknown`: reviver ou enterrar?**
Reviver custa um call site no funil de inbound; enterrar custa admitir que um plugin não pode
criar media type. **Recomendação: decidir explicitamente**, e nunca deixar como está — um
gancho documentado que não é chamado é pior que gancho nenhum.

**P3 — A decisão (b) do C1 (tirar `/protocolos` do roteador do core)?**
Custa um teste e o hardcode de SPA. **Recomendação: ⏸️ adiar** — a F0 já entrega 99% do ganho
com risco zero.

**P4 — Versionar a superfície de import (R1) agora ou junto do 83?**
É pré-requisito do 83, não deste plano. **Recomendação:** decidir no 83, mas registrar aqui
que **F4/F5 do 83 não devem rodar** sem uma das duas saídas (contract test contra os zips, ou
superfície pública versionada).
