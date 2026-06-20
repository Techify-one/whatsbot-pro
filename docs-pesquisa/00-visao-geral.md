# WhatsBot Pro — Pesquisa de Arquitetura (Visão Geral)

> **Status:** Fase de PESQUISA (anterior ao planejamento). Nada de código é alterado aqui.
> Estes documentos servem para você estudar as tecnologias, comparar alternativas e
> depois montar um plano de implementação por feature.

## Contexto e decisões já tomadas

Premissas validadas com o time (2026-06-18):

| Tema | Decisão |
|------|---------|
| **Tenancy / deploy** | **Uma empresa, hospedado em servidor único** (Coolify/Docker). Multi-usuário da mesma empresa. **Sem multi-tenant** (sem isolamento entre clientes) por enquanto — mas o desenho não deve fechar portas para isso. |
| **Motor multi-agente** | **Agno**. Decisão "embutido no processo do WhatsBot" vs "serviço Python separado" ainda aberta → ver trade-offs em [`06-motor-multiagente-agno.md`](06-motor-multiagente-agno.md). |
| **Config do motor IA no banco** | **Decidido: agentes, prompts, variáveis E o código das tools ficam no banco (code-in-DB)**, no estilo `/opt/gerenciamento-ia` — para mudar comportamento sem mexer em código/deploy e permitir que a própria IA debugue/crie tools. Modelo de ameaça é baixo (empresa única, servidor próprio, só admin/IA escrevem). Avaliar empacotar o motor como **plugin**. Exige materialização+`pip install`+reload, versionamento/histórico e auditoria. Ver [`06-motor-multiagente-agno.md`](06-motor-multiagente-agno.md). |
| **Modelo de conversa** | **Decidido: estilo Chatwoot** — `Contact` (pessoa) → `ContactInbox` (identidade da pessoa num canal/número) → `Conversation` (várias por contato, reabríveis). Mesmo contato em números diferentes = conversas diferentes. Schema modelado no formato final desde já; UI pode simplificar no MVP. Ver [`01-inbox-e-conversas.md`](01-inbox-e-conversas.md). |
| **Canais** | **Abstração de canal genérica desde já** (`Channel`/`Provider`). GOWA (não-oficial), WhatsApp Cloud API (oficial) e, no futuro, Telegram/Instagram/Email são implementações da mesma interface. |
| **Caixa de entrada = core; providers plugáveis** | **Decidido: o domínio de atendimento (Inbox/Conversa/Message), o contrato de canal e o roteamento de entrada são CORE** — o sistema é um sistema de atendimento, não vive sem inbox. O GOWA deixa de ser core cravado e passa a ser um *provider* atrás do contrato. **Decidido (2026-06-18): o GOWA já nasce como provider-PLUGIN no v1** (não built-in temporário) — para quem só usa Cloud API/Telegram/e-mail não rodar o GOWA. Isso exige construir antes as **3 capacidades de runtime no CORE**: (1) lifecycle de plugin (`setup`/`teardown` aguardados), (2) supervisor de tasks de fundo, (3) serviço de subprocesso gerenciado. As 3 são CORE (habilitam plugins; não podem ser plugin). Ver princípio abaixo e [`02-canais-e-providers.md`](02-canais-e-providers.md). |
| **Cloud API oficial** | Modo "tradicional" (cliente cria o app na Meta e fornece token), **não** parceiro de tecnologia/embedded signup. |
| **Entregável** | Pasta `docs-pesquisa/`, tudo em Markdown, um arquivo por feature. |

## O ponto de partida (o que o WhatsBot é hoje)

Resumo do estado atual relevante para a versão Pro (mapeado do código):

- **Sem usuários.** Autenticação é uma **senha única compartilhada** por toda a instância
  (`server/auth.py`, middleware em `server/app.py`). Não há conceito de "quem fez o quê".
- **Config global key-value.** Tabela `config` (chave→valor JSON). Um único `system_prompt`,
  uma única API key, um único modelo — tudo global. Não há escopo por inbox/usuário.
- **Um número só.** `gowa/manager.py` sobe **um** subprocess GOWA na porta 3000; `gowa/client.py`
  usa `device_id = "whatsbot"` hardcoded. O webhook não sabe "de qual número" a mensagem veio.
- **Contato é a unidade, não a conversa.** Tabela `contacts` é plana (chave = `phone`). Não existe
  o conceito de "conversa" com status (aberta/fechada), atribuição a atendente, ou transferência.
- **Um agente global.** `agent/handler.py` é um singleton com prompt/modelo/tools globais.
  Tools são registradas em código (`CORE_TOOLS`) + plugins; overrides ficam em `tool_overrides`.
- **Frontend Preact sem build.** Telas em `web/static/js/components/`; roteamento SPA em `app.js`;
  telas extras entram pelo `GearMenu`. Eventos real-time via WebSocket (`server/state.py`).
- **Sistema de plugins maduro.** Bus de **events** (fire-and-forget) e **filters** (interceptivos)
  cobrindo quase todo o pipeline. Muitas features novas podem (e devem) reusar esse bus.

> Implicação central: as três suposições "**1 número / 1 agente / sem usuários**" estão
> espalhadas pelo código. A maior parte do esforço Pro é introduzir três entidades novas —
> **Usuário**, **Inbox/Canal** e **Conversa** — e re-escopar o que hoje é global para elas.

## Modelo de domínio alvo (visão de conjunto)

```
User ───< InboxMember >─── Inbox ───1:1─── Channel(Provider: gowa | cloud_api | telegram | ...)
 │                           │
 │                           │ 1:N
 │                           ▼
 └──assigned_to──< Conversation >── contact ─── Contact
                       │  status: open|pending|resolved|snoozed
                       │  assignee, team, custom_attributes
                       ▼
                   Message (já existe; ganha conversation_id)

Agent (config no banco: prompt/model/tools/vars) ──participa de──> Inbox
AuditLog ── registra ações de ── User
QuickReply ── escopo ── Inbox/global
CustomAttributeDef ── aplica a ── Contact | Conversation
```

## Princípio arquitetural: caixa de entrada é CORE; provider é implementação

A versão Pro reposiciona o que o sistema **é**. Hoje o WhatsBot é "um bot com IA que
conversa no WhatsApp" — o GOWA está cravado no core (`gowa/manager.py`, `gowa/client.py`)
de forma não-abstraída. A versão Pro é **um sistema de atendimento que recebe mensagens por
canais**, no qual a IA é *um* dos atendentes. O sistema não vive sem uma caixa de entrada.

Disso decorre a fronteira **core vs plugin** que guia todo o resto:

- **É CORE (o sistema não existe sem isso):** o domínio de atendimento
  (`Inbox` → `Conversation` → `Message`, ciclo de vida, atribuição), o **contrato de canal**
  (interface `ChannelProvider`: receber/enviar/ler/conectar), o **roteamento de entrada**
  (webhook → descobrir canal/inbox → criar/continuar conversa) e o **registry de canais**.
- **NÃO precisa ser core: o provider concreto.** GOWA, Cloud API e Telegram são
  *implementações* atrás do contrato. Uma `Inbox` **tem** um `Channel`; o GOWA não "vira"
  uma inbox — ele é o *provider* de uma inbox WhatsApp não-oficial.

> **Distinção que vale fixar:** `Inbox` = a caixa lógica (nome, atendentes, fila, config).
> `Channel/Provider` = *como* aquela inbox fala com o mundo. Separar os dois é o que permite
> "trocar o GOWA por Cloud API" ou "ter dois números" sem mexer no domínio de atendimento.

**O mesmo padrão que o WhatsBot já usa para *tools* se repete para *canais*:** o core define
o registry e o contrato; **core e plugins registram implementações no mesmo registry.**

| Camada | Onde vive | Exemplos |
|--------|-----------|----------|
| Contrato + registry de canal | **Core** | `ChannelProvider`, `ChannelRegistry`, roteamento de webhook |
| **Capacidades de runtime** que os providers consomem | **Core** | supervisor de tasks de fundo; serviço de subprocesso gerenciado; lifecycle de plugin (`setup/teardown`) |
| Providers (qualquer tipo) | **Core OU plugin** | GOWA, Cloud API, Telegram, Instagram, Email |

**Meta de arquitetura (decisão 2026-06-18):** que *qualquer* provider possa ser plugin —
inclusive o GOWA — para que um cliente que só usa WhatsApp oficial (Cloud API), só Telegram ou
só e-mail **não instale nem rode o GOWA**. O sistema é um sistema de atendimento; os canais são
peças plugáveis. A investigação confirmou que isso é **viável, mas exige fundação no core**
(detalhe em [`02-canais-e-providers.md`](02-canais-e-providers.md)). O custo depende do *tipo* de provider:

| Tipo de provider | Precisa em runtime | Vira plugin com | Custo |
|---|---|---|---|
| **Webhook-only** (Cloud API, Telegram via webhook) | nada de fundo | ponto de extensão de canal + roteamento de webhook | baixo |
| **Polling leve** (Telegram long-poll, IMAP/e-mail) | 1 corrotina em loop | + **supervisor de tasks de fundo** | médio |
| **Subprocesso** (GOWA, binário Go) | processo do SO + watchdog | + **serviço de subprocesso gerenciado** (mata a árvore, die-with-parent, limpa órfão no boot) | alto |

O sistema de plugins **hoje não suporta** subprocesso/loop de fundo com segurança (não há
gancho de lifecycle aguardado; o toggle usa `os._exit(0)` que pula finalizers — ver doc 02).
Por isso a fronteira não é "GOWA é core para sempre", e sim: **construir as capacidades de
runtime no core e, sobre elas, o GOWA pode ser um provider-plugin opcional.** É o mesmo
princípio unificador do motor de IA ([`06-motor-multiagente-agno.md`](06-motor-multiagente-agno.md)):
*núcleo, contratos e capacidades no core; implementações em core e/ou plugins.*

## Índice dos documentos

| # | Documento | Cobre as suas features |
|---|-----------|------------------------|
| 01 | [`01-inbox-e-conversas.md`](01-inbox-e-conversas.md) | Caixa de entrada estilo Chatwoot; abrir/encerrar/atribuir/transferir conversas; tela de não-atribuídas; participação de agente por inbox |
| 02 | [`02-canais-e-providers.md`](02-canais-e-providers.md) | Abstração de canal; múltiplos números no mesmo GOWA; WhatsApp Cloud API oficial; base para Telegram/IG/Email |
| 03 | [`03-rbac-usuarios-permissoes.md`](03-rbac-usuarios-permissoes.md) | Usuários, grupos de acesso (adm/atendente/plugins); RBAC vs ABAC vs ReBAC |
| 04 | [`04-respostas-rapidas.md`](04-respostas-rapidas.md) | Atalhos tipo `/oi-anna` → mensagem pré-digitada |
| 05 | [`05-atributos-personalizados.md`](05-atributos-personalizados.md) | Atributos custom de contato e de conversa |
| 06 | [`06-motor-multiagente-agno.md`](06-motor-multiagente-agno.md) | Multi-agentes com Agno; config (tools/prompts/agentes/variáveis) no banco; embutido vs serviço separado |
| 07 | [`07-auditoria.md`](07-auditoria.md) | Trilha de auditoria de ações de usuário |
| 08 | [`08-filtros.md`](08-filtros.md) | Melhoria dos filtros com base nas novas entidades |

> Cada documento segue a mesma estrutura: **o que existe hoje → alternativas de tecnologia/
> abordagem com trade-offs → recomendação → impacto no schema/código → faseamento → perguntas
> em aberto**.

## Referência: o motor de IA do `/opt/gerenciamento-ia`

Você citou esse projeto como inspiração do motor multi-agente. Resumo do que ele faz
(detalhado em [`06-motor-multiagente-agno.md`](06-motor-multiagente-agno.md)):

- Tabelas no Postgres: `gerenciamento_ia_agentes` (agent_key, `model_config` JSONB,
  `prompt_template`, `tool_names[]`, `hooks_config` JSONB, `routing_targets[]`, `is_router`,
  `version`), `gerenciamento_ia_tools` (`name`, `code` = fonte Python, `dependencies[]`,
  `install_status`, `version` + histórico), `gerenciamento_ia_prompts`, `gerenciamento_ia_variaveis`.
- `dynamic_registry`: cache em memória com polling (TTL ~60s) de agentes/tools versionados.
- `agent_factory.build_agent(key, ...)`: monta um `agno.Agent` a partir da config do banco.
- `tool_installer`: materializa o `code` em `.py`, roda `uv pip install` das deps e dá `importlib.reload`.
- Roteamento multi-hop via tool `transferir_para_outro_agente` (profundidade máx. 5).
- **O que cortar no início (você pediu enxuto):** embeddings/pgvector, produtos, ofertas.

## Como usar estes docs

1. Leia este overview e o doc da feature que vai atacar primeiro.
2. Cada doc tem uma seção **"Perguntas em aberto"** — responda-as antes de planejar.
3. O faseamento sugerido (abaixo) é só um ponto de partida; reordene conforme prioridade de negócio.

### Faseamento sugerido (rascunho — validar no planejamento)

1. **Fundação:** Usuários + RBAC ([03]) e introdução das entidades `Inbox`/`Conversation` ([01]).
   Sem isso, o resto não tem onde se ancorar.
2. **Canais:** abstração de provider + GOWA multi-número ([02]); Cloud API logo em seguida.
3. **Operação:** ciclo de vida de conversa, atribuição/transferência, não-atribuídas ([01]);
   respostas rápidas ([04]); atributos custom ([05]); filtros ([08]).
4. **IA:** motor multi-agente Agno ([06]).
5. **Transversal:** auditoria ([07]) — começar cedo no que for barato (decorator + tabela).
