# Plano 144 — Suporte ao aluno vira plugin: a IA da Escola responde pelo WhatsBot, não pelo Chatwoot

> **Status:** EXECUTADO F1–F7 (2026-08-28) — **F0 pendente** (infra manual: usuário read-only no Postgres do LMS + canal *Site (Widget)*) e **F8 pendente** (cutover no LMS, PR próprio no repositório `/opt/lms`). O plugin `escola_ia` 1.0.0 está em `whatsbot-pro-plugins/plugins/escola_ia/`, com ZIP e entrada no catálogo. · **Data:** 2026-08-28 · **Escopo:** plugin novo (`escola_ia`); **zero** mudança no core do WhatsBot. O LMS é tocado só no front (troca do widget + envio do contexto) e o Chatwoot sai do caminho na última fase.
> **Origem:** pedido da usuária — criar no WhatsBot Pro um plugin que faça o mesmo que o `vendas_ia`, usando os prompts e as tools do próprio WhatsBot, com as variáveis do painel do Nexus como configuração, mais a URL do banco de onde saem as informações e transcrições de cursos e aulas. O plugin deve **substituir** a funcionalidade do LMS que hoje responde pelo Chatwoot.
> **Método:** leitura do código real dos três lados (LMS em `/opt/lms`, motor de IA em `~/opt/nexus-tech-gerenciamento-ia`, core e plugins do WhatsBot em `~/opt/whatsbot-pro` e `~/opt/whatsbot-pro-plugins`), telemetria de produção (`gerenciamento_ia_executions`, banco Nexus Techify) e sondagem do Postgres do WhatsBot. Todo `arquivo:linha` abaixo foi verificado em 2026-08-28. Plugin de referência: `vendas_ia` 1.8.0 (catálogo em banco externo + tools de busca + seed de agentes); referência secundária: `lms_login` 1.1.0 (credencial do banco da Escola numa URL só).
> **O quê/porquê:** hoje o LMS é dono de uma camada que não é dele — ele carimba o contexto da aula no contato do Chatwoot, o Chatwoot chama o motor Agno do gerenciamento-ia e é lá que mora o prompt, a tool e a decisão. Essa cadeia está **quebrada no último trecho** desde 10/07/2026 (§2.4) e o aluno só recebe transferência para humano. Depois deste plano, quem fala com o aluno é o WhatsBot: o acervo continua no banco do LMS, mas o agente, o prompt, as tools, o histórico e o painel são os nativos.
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões da usuária / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| D1 ✅ (2026-08-28) | O aluno conversa pelo **widget de site do WhatsBot** embutido no LMS, no lugar do widget do Chatwoot | O canal é o plugin `website` 1.1.2, que já tem sessão por visitante e `identify` por HMAC (`plugins/website/src/routes.py:264`). Nada de canal novo |
| D2 ✅ (2026-08-28) | O contexto da aula é **enviado pelo LMS**, no **Caminho A**: a página do curso chama uma rota pública do próprio `escola_ia` com o e-mail do aluno, o HMAC e curso/módulo/aula | Não mexe no plugin `website` (que é de outro domínio) e não depende do token de sessão, que vive dentro do iframe e é inalcançável pela página. O LMS já sabe assinar um e-mail com HMAC — faz isso hoje para o Chatwoot (`/opt/lms/backend/src/index.ts:267`) |
| D3 ✅ (2026-08-28) | O **operador escolhe** entre busca léxica e híbrida | Setting `search_mode`, lido a cada busca (mesmo desenho de `vendas_ia/src/search.py`). Léxica é o padrão: nenhuma chamada de rede dentro da tool |
| D4 ✅ (2026-08-28) | No modo híbrido, os chunks e vetores ficam no banco do **WhatsBot** | `pgvector 0.7.4` está **disponível** nesse Postgres (sondado em 2026-08-28; `pg_trgm` e `unaccent` já instalados). O banco do LMS continua **estritamente read-only** — some a exigência de pgvector e de GRANT de escrita lá, que é o que travou a solução anterior |
| D5 ✅ (2026-08-28) | As settings do plugin são **as variáveis da tela do Nexus** mais a URL do banco do LMS | §3.2. O que o WhatsBot já resolve nativamente (modelo do agente, filtro de histórico, visão/transcrição) **não** vira campo novo |
| D6 ✅ (2026-08-28) | Há uma **segunda URL opcional**, para o banco de dúvidas frequentes | Habilita a quarta tool, `pesquisar_perguntas_frequentes`, sobre `respostas_duvidas_escola`. Vazia ⇒ a tool não é semeada e nada quebra |
| D7 ✅ (2026-08-28) | Os prompts e as tools são **os do WhatsBot** | Agente em `ai_agents` (seed não-destrutivo), tools em `ai_tools` com `kind='code'` — editáveis, versionadas e apagáveis na tela Tools nativa, igual ao `vendas_ia` (`vendas_ia/src/tools_seed.py:42`) |
| D8 ✅ | **Zero mudança no core do WhatsBot** | Tudo no plugin, imports defensivos, tabelas `plugin_escola_ia_*` — regra do `CLAUDE.md` §"O que fica no core e o que vai pro plugin" |
| D9 ✅ (2026-08-28) | `student_last_lesson` é **rede de segurança**, não a fonte primária | Quando o contexto do D2 não chegou (aluno escreveu no dia seguinte, widget não carregou), a aula em foco vem da última aula acessada, resolvida no servidor |

---

## 1. Resumo executivo

O aluno abre uma aula em `escola.techify.one`, clica no balão de chat e pergunta "como o professor fez aquilo?". Para responder, alguém precisa saber **quem é o aluno**, **em que aula ele está** e **o que foi dito naquela aula**.

Hoje esse alguém é o motor Agno do gerenciamento-ia, alcançado pelo Chatwoot. O contexto chega perfeito (o LMS o carimba no contato como `chat_info`), o modelo entende a pergunta — e a resposta é uma transferência para atendente humano, porque as duas pontas que levam ao conteúdo estão quebradas (§2.4).

Depois deste plano, quem responde é o WhatsBot. O plugin `escola_ia` traz para dentro dele as quatro coisas que hoje moram fora:

1. **A identidade e a matrícula** — quem é o aluno e em quais cursos ele tem acesso vigente, lidos direto no banco do LMS.
2. **A aula em foco** — recebida do LMS numa rota pública assinada, injetada no prompt como o fragmento "AULA EM FOCO".
3. **O acervo** — as transcrições das aulas, buscadas em modo léxico (sem infra) ou híbrido (com vetores no banco do WhatsBot).
4. **O agente** — semeado em `ai_agents`, com o prompt adaptado do Nexus e a ordem obrigatória "matrícula antes de conteúdo".

O ganho não é só consertar o que quebrou. O atendimento passa a existir no painel do WhatsBot (hoje o diálogo vive num Chatwoot separado), o histórico e a memória são os nativos, o prompt e as tools ficam editáveis por quem opera, e some um serviço inteiro do caminho quente.

---

## 2. Como funciona hoje (mapa verificado)

### 2.1 O ciclo completo

| # | Onde | O que acontece |
|---|---|---|
| 1 | `/opt/lms/frontend/src/pages/CoursePage.tsx:78` | A cada aula selecionada, a página grava curso/módulo/aula (id + nome) no `CourseContext` |
| 2 | `/opt/lms/frontend/src/components/ChatwootWidget.tsx:14` | Monta o objeto `chat_info` = `{aluno{id,nome,email,telefone}, curso, modulo, aula}` |
| 3 | `/opt/lms/frontend/src/hooks/useChatwoot.ts:130` | Identifica o aluno no Chatwoot com `setUser(email, {identifier_hash})`; o HMAC vem de `GET /chatwoot/hash` (`/opt/lms/backend/src/index.ts:267`) |
| 4 | `useChatwoot.ts:69`, `:139`, `:148` | Publica `chat_info` como **custom attribute do contato** — na inicialização, ao abrir o widget e a cada mensagem enviada |
| 5 | Chatwoot (inbox 6 "Escola") | Webhook `message_created` → `nexus.techify.run/api/v1/webhook/chatwoot` (controller Nest) → repassa para o FastAPI em `127.0.0.1:8006/webhook/chatwoot` |
| 6 | `~/opt/nexus-tech-gerenciamento-ia/python/src/webhooks/chatwoot_handler.py:215` | Lê `sender.custom_attributes.chat_info`, desserializa e **prefixa a mensagem** com o bloco `[Contexto do cliente]` contendo `curso_id`, `modulo_id` e `aula_id` |
| 7 | `python/src/agents/suporte.py` | O agente SUPORTE (gpt-5.2 via OpenRouter) carrega o prompt do banco e recebe nove tools, com o telefone do aluno fixado por `functools.partial` |
| 8 | `python/src/tools/verificar_matricula.py`, `pesquisar_conteudo_aula.py`, `buscar_transcricao.py` | Portão de matrícula, depois busca híbrida em `lms_lesson_chunks` ou a transcrição inteira de `lessons.video_transcript` |

### 2.2 Regras de negócio que precisam ser preservadas

Todas vivem nas tools do gerenciamento-ia:

- **Portão de matrícula antes de qualquer conteúdo** (`python/src/agents/prompts/suporte_prompt.py:12`): `verificar_matricula_aluno` é a primeira ação obrigatória sempre que o aluno menciona aula, curso ou turma.
- **Identidade resolvida pelos últimos 8 dígitos do telefone** (`python/src/services/database.py:298`): ignora +55, DDD e o nono dígito, porque os dois lados guardam telefone em formatos inconsistentes. Cursos vêm de `v_student_course_access` com `expires_at > NOW()`.
- **A identidade do aluno é invisível para o LLM**: entra por `partial` como `_allowed_phone`, fora da assinatura que o modelo enxerga. O modelo não consegue consultar o conteúdo de outro aluno nem remover o filtro.
- **Preferência pelo `aula_id`** (`python/src/tools/buscar_transcricao.py:56`): com o id do contexto, resolve por chave primária; sem ele, cai para `ILIKE` por nome.
- **Só uma aula por resposta** (`python/src/tools/pesquisar_conteudo_aula.py:127`): entre os 5 trechos devolvidos, ficam apenas os da aula de maior score, para o modelo não misturar explicações de aulas diferentes.
- **Handoff sem detalhe técnico** (`pesquisar_conteudo_aula.py:150`): aluno com matrícula ativa e conteúdo não encontrado gera `status: transferir_para_atendente` com instrução explícita de **não** mencionar transcrição, indexação ou motivo técnico.
- **Chunking com sobreposição** (`python/src/services/chunker.py:9`): ~500 tokens por trecho (≈385 palavras) com 50 de sobreposição, para não cortar uma explicação ao meio.

### 2.3 O que o WhatsBot já oferece (ganchos verificados)

| Precisa | Gancho | Onde |
|---|---|---|
| Rota que o navegador do aluno chama sem token de operador | Qualquer rota sob `/api/plugins/<id>/public/` é **isenta de auth** pelo core | `plugins/website/src/routes.py:4` (comentário do contrato) |
| Injetar contexto no system prompt | `PROMPT_FRAGMENTS` do plugin, concatenados no turno | `agent/prompt_builder.py:131`; exemplo em `vendas_ia/src/prompts.py` |
| Forçar o agente certo sem gastar um turno de roteador | `filter.agent.resolve` (síncrono, sem race) | `vendas_ia/src/filters.py` |
| Tools editáveis pelo operador | `ai_tools` com `kind='code'`, rodando em subprocesso isolado | `agent/tool_isolation.py`, `vendas_ia/src/tools_seed.py:42` |
| Ler um banco de terceiro sem contaminar o do WhatsBot | 2ª engine SQLAlchemy read-only | `vendas_ia/src/nexus_db.py` |
| Identidade verificada do visitante no widget | `POST /public/identify` com `identifier` + `identifier_hash` | `plugins/website/src/routes.py:264` |
| Busca vetorial | `pgvector 0.7.4` **disponível** (não instalado) no Postgres do WhatsBot; `pg_trgm` e `unaccent` já instalados | sondagem 2026-08-28 |
| Modelo que conversa | Cascata `ai_agents.model_config` → `ai_variables` por agente → global, com proxy compatível com OpenRouter | `ai_engine/model_factory.py` |
| Filtro de histórico por regex | `ai_history_exclude_patterns` (global, fail-open) | `agent/history_filter.py:41` |

⚠️ **O subprocesso de tool tem teto DURO de 10 s** (`agent/tool_isolation.py:54`), do qual ~1,4 s vai no bootstrap. É por isso que o `vendas_ia` fixou 3 s no cliente de embedding (`vendas_ia/src/embeddings.py:37`): sem teto explícito o SDK da OpenAI espera minutos, a tool é **morta**, o modelo recebe "excedeu o tempo limite" e **reenvia o histórico inteiro** na retentativa — um atendimento chegou a 167 mil tokens. Este plugin nasce com o mesmo teto.

### 2.4 Três achados do lado atual (por que a cadeia não responde hoje)

1. **O banco do LMS recusa a conexão do motor de IA.** Desde **10/07/2026**, toda chamada de `verificar_matricula_aluno` e `buscar_transcricao_aula` volta com `FATAL: password authentication failed for user "postgres"` em `database.onlinecenter.com.br`. Reproduzido localmente com as credenciais do `.env` do serviço.
2. **A URL de sync de embeddings não existe.** O LMS chama `…/api/v1/webhook/embeddings/sync` (`/opt/lms/backend/src/services/embeddingSyncService.ts`), mas o Nest só faz proxy de `/webhook/chatwoot`; o endpoint real é `/admin/embeddings/sync`, no FastAPI que roda em `127.0.0.1:8006`, sem rota pública. `GET` na URL documentada devolve **404**. Como a chamada do LMS é fire-and-forget com `console.warn`, o erro nunca aparece para ninguém.
3. **A busca de conteúdo nunca devolveu um trecho.** Em todo o histórico, `pesquisar_conteudo_aula` foi chamada 21 vezes e **nenhuma** retornou `status: encontrado` — sempre `transferir_para_atendente`, que é o retorno de zero linhas. Coerente com `lms_lesson_chunks` vazio, efeito direto do item 2.

**A matéria-prima existe:** consulta à API do LMS em 2026-08-28 mostra **66 das 78 aulas com transcrição**, várias com dezenas de milhares de caracteres (a maior com 73.609). O que falta é quem leia.

---

## 3. Desenho do plugin

### 3.1 Manifesto e arquivos

```
plugins/escola_ia/
  src/plugin.yaml            entry: filters, prompts, settings, routes, events
  src/settings.py            variáveis do painel + as duas URLs de banco
  src/_config.py             leitura com os mesmos defaults (espelho)
  src/lms_db.py              2ª engine READ-ONLY para o banco do LMS
  src/faq_db.py              3ª engine READ-ONLY, opcional (dúvidas frequentes)
  src/enrollment.py          aluno + cursos vigentes
  src/context.py             aula em foco (rota pública + fallback no banco)
  src/search.py              busca léxica e híbrida
  src/embeddings.py          embedding da query, com teto de 3 s
  src/indexer.py             chunking + vetores (só no modo híbrido)
  src/prompts.py             PROMPT_FRAGMENT "AULA EM FOCO"
  src/filters.py             filter.agent.resolve + ignored_messages
  src/agents_seed.py         agente suporte_escola (não-destrutivo)
  src/tools_seed.py          tools kind='code'
  src/tool_code/             verificar_matricula_aluno · pesquisar_conteudo_aula
                             buscar_transcricao_aula · pesquisar_perguntas_frequentes
  src/seed_prompts/suporte_escola.md
  src/routes.py              /status · /seed · /reindex · /public/contexto
                             + PUT write-only da URL de FAQ (nunca devolvida)
  src/migrations/001_initial.sql · 002_vector.sql
  src/static/config.js       tela de diagnóstico
  tests/python/              busca, matrícula, contexto, HMAC, degradação
```

`plugin.yaml`, campos que importam:

```yaml
id: escola_ia
name: Escola IA (LMS)
whatsbot_api_version: ">=1.0,<2.0"
entry:
  settings: settings
  filters: filters      # filter.agent.resolve · filter.outbound.text
  prompts: prompts      # PROMPT_FRAGMENT "AULA EM FOCO"
  routes: routes        # diagnóstico + rota pública de contexto
  events: events        # app.startup
screens:
  - id: escola-ia-panel
    title: Escola IA
    path: /escola_ia
    config: false       # menu da engrenagem; a config é o form declarativo
    component: /plugins/escola_ia/static/config.js
    requires: view
permissions: [db.write, llm.tool]
migrations: migrations
```

### 3.2 Settings (`plugin.escola_ia.*`)

As variáveis do painel do Nexus, traduzidas. O critério: **só vira campo o que o WhatsBot ainda não resolve.**

| Variável no Nexus | No plugin | Situação |
|---|---|---|
| — | `credentials` · URL do banco do **LMS** | **novo** |
| — | URL do banco de **dúvidas frequentes** (opcional) — por rota write-only, não pelo form | **novo** (D6) |
| `openrouter_api_key` | `openrouter_api_key` — só para o embedding da pergunta | campo |
| `openrouter_base_url` | `openrouter_base_url` (default `https://openrouter.ai/api/v1`) | campo |
| `model_embeddings` | `embedding_model` + `embedding_dims` | campo |
| `openrouter_http_referer` | `openrouter_http_referer` | campo |
| `openrouter_x_title` | `openrouter_x_title` | campo |
| `openrouter_capture_real_cost` | `openrouter_capture_real_cost` | campo |
| `ignored_messages_user` | `ignored_messages_user` — mensagens do aluno que a IA não responde | campo |
| `ignored_messages_ia` | `ignored_messages_ia` — bloqueia o envio no `filter.outbound.text` | campo |
| `model_image_analysis` | visão e transcrição já são do WhatsBot | **já nativo** |
| `openrouter_models_cache_ttl` | catálogo de modelos é tela do Nexus | **não migra** |

Mais quatro campos de comportamento, sem par no Nexus:

| Campo | Default | Papel |
|---|---|---|
| `search_mode` | `lexica` | `lexica` ou `hibrida`. Lido a cada busca — trocar vale na seguinte, sem restart (D3) |
| `hybrid_semantic_weight` / `hybrid_fulltext_weight` / `hybrid_limit` | 0.7 / 0.3 / 5 | Pesos e teto da busca |
| `target_agent_key` | `suporte_escola` | Agente que o `filter.agent.resolve` força para aluno reconhecido |
| `context_ttl_seconds` | 1800 | Por quanto tempo o contexto recebido do LMS vale antes de o plugin cair no fallback do banco |

⚠️ **O campo do LMS chama-se `credentials` de propósito, e o nome é a proteção.** A URL carrega a senha dentro dela, e o sanitizador da trilha de auditoria compara o **nome** da chave — `_SECRET_KEYS` em `db/repositories/audit_repo.py:21` é um `frozenset` casado por **igualdade exata** de `k.lower()` (`:33`), e já traz `credentials`. Um campo chamado `lms_url`, `database_url` ou `dsn` publicaria a senha **em claro** na tela de Auditoria. Mesma decisão do `lms_login` — não "melhore" esse nome.

⚠️ **A segunda URL não pode simplesmente herdar esse truque** (verificado em 2026-08-28): como o casamento é por igualdade exata, `faq_credentials` **não** seria mascarada. E há uma segunda exposição, independente da trilha: `GET /api/plugins/<id>/settings` devolve os valores do form **em claro** (`format: password` é inerte — é o que o `vendas_ia` documenta em `settings.py` ao tirar o token da Meta do formulário). Portanto a URL de FAQ entra por **rota write-only**, no padrão de `vendas_ia/src/routes.py`: gravada por `PUT`, nunca devolvida, e exibida apenas como `postgresql://usuario:***@host/banco` na tela de diagnóstico. A URL do LMS mantém o campo no form (mesmo trade-off que `lms_login` e `vendas_ia` já aceitam hoje para `credentials` e `nexus_dsn`); migrá-la também para a rota write-only fica como melhoria opcional na F5.

⚠️ **A chave do OpenRouter é só para embeddings.** O modelo que **conversa** é o do WhatsBot (`ai_agents.model_config` + `ai_variables`). Sem a chave, o modo híbrido degrada para léxico em vez de falhar — mesma regra do `vendas_ia`.

⚠️ **As duas URLs nascem vazias.** São segredos de bancos de outros produtos e não podem viajar dentro do ZIP publicado. Sem a URL do LMS o plugin fica **inerte e logado** (nunca meio funcionando); sem a de FAQ, apenas a quarta tool não é semeada.

### 3.3 As conexões read-only

Cópia do desenho de `vendas_ia/src/nexus_db.py`, com dois detalhes que o `lms_login` já provou necessários:

- **Normalização do driver**: uma URL colada como `postgresql://` faz o SQLAlchemy escolher `psycopg2`, que **não** está instalado (só `psycopg` v3). O plugin reescreve o esquema para `postgresql+psycopg://`, preservando qualquer `+driver` explícito.
- **Teste de fumaça ao salvar**: quando os campos de conexão mudam, o save valida conexão, colunas e permissões antes de gravar, e a falha aparece em vermelho no próprio formulário. Credencial que não conecta **não é gravada em silêncio** — foi exatamente esse modo de falha que deixou a solução atual quebrada por 49 dias sem ninguém notar (§2.4).
- Engine singleton chaveada pela URL (editar reconstrói sem restart), `pool_pre_ping`, `pool_recycle=1800`, timeout curto de conexão. Todas as queries em `connect()`, nunca `begin()`.

**Privilégios mínimos no banco do LMS** — usuário dedicado, nunca o dono:

```sql
GRANT SELECT ON students, courses, modules, lessons,
                v_student_course_access, student_last_lesson TO escola_ia_ro;
```

Nada além disso. O plugin não escreve uma linha no banco da Escola (D4).

### 3.4 Identidade do aluno e matrícula

O widget identifica o aluno pelo **e-mail assinado por HMAC** (`plugins/website/src/routes.py:264`) — a mesma identidade que o LMS já usa hoje no Chatwoot. Então a resolução no plugin é:

1. `identifier` da sessão do canal (e-mail) → `students.email`.
2. Se não houver identidade verificada, cai no telefone do contato, pelos **últimos 8 dígitos** (`REGEXP_REPLACE(s.phone,'[^0-9]','','g') LIKE '%' || last8`), preservando a regra de §2.2.
3. Cursos vigentes por `v_student_course_access` com `expires_at IS NULL OR expires_at > NOW()`.

Sem identidade nenhuma, o agente **não adivinha**: pede o e-mail ou transfere. Um aluno anônimo no widget não pode receber conteúdo de curso.

### 3.5 Contexto da aula — Caminho A (D2)

**Por que não pelo widget.** O SDK do host expõe apenas `window.WhatsBotChat = {run, open, close, toggle}` (`plugins/website/src/static/sdk.js:132`) — não existe equivalente de `setCustomAttributes`, e o token de sessão vive **dentro do iframe**, fora do alcance da página do curso. Levar o contexto por ali exigiria subir versão do plugin `website`.

**Como fica.** A página do curso do LMS chama, na troca de aula:

```
POST /api/plugins/escola_ia/public/contexto
{
  "identifier": "aluno@exemplo.com",
  "identifier_hash": "<hmac-sha256 do e-mail com o hmac_token do canal>",
  "widget_token": "<token público do canal>",
  "contexto": {
    "curso":  {"id": "...", "nome": "..."},
    "modulo": {"id": "...", "nome": "..."},
    "aula":   {"id": "...", "nome": "..."}
  }
}
```

O plugin:

1. Resolve o canal pelo `widget_token` e lê o `hmac_token` daquele canal (`channel_credential_repo`).
2. Confere o `identifier_hash` em **comparação de tempo constante**. Assinatura inválida ⇒ 200 com `{ok:false}`, sem detalhe (é rota pública).
3. Faz upsert em `plugin_escola_ia_contexto` pela identidade, com `updated_at`.
4. Espelha em `custom_attributes` da conversa aberta (curso e aula), para o operador ver no painel — mesmo espírito do `mirror_offer_attribute` do `vendas_ia`.

Na montagem do prompt, `prompts.py` resolve a identidade da conversa → contexto → bloco:

```
## AULA EM FOCO
O aluno está assistindo à aula abaixo. Use `aula_id` diretamente nas tools de conteúdo.
- Curso: Comece por aqui !   (curso_id: 3e52eb75-…)
- Módulo: Introdução          (modulo_id: c51375df-…)
- Aula: Acesso as ferramentas (aula_id: 8d2d3863-…)
```

**Rede de segurança (D9):** contexto ausente ou mais velho que `context_ttl_seconds` ⇒ o plugin consulta `student_last_lesson` (LMS, migration 005; a consulta de referência está em `/opt/lms/backend/src/controllers/lastLessonController.ts:59`) e usa a última aula acessada, marcando no bloco que a aula veio do histórico, não da tela.

⚠️ **A rota é pública e recebe dados de aluno.** Ela não devolve nada além de `{ok}`, tem rate-limit por identidade e **nunca** é auditada com o corpo (é tráfego de cliente final, alto volume) — mesma decisão de escopo do `website` (`plugins/website/src/routes.py:44`).

### 3.6 As tools

Semeadas em `ai_tools` com `kind='code'` (seed **não-destrutivo**: só cria se o nome ainda não existe), código versionado em `tool_code/`. Ligar, editar, desligar e apagar é na tela Tools nativa — o plugin só provê a infra que elas chamam.

| Tool | Papel | Lê |
|---|---|---|
| `verificar_matricula_aluno` | Portão obrigatório. Devolve `matriculado_no_curso`, `nao_matriculado_no_curso`, `matriculado`, `sem_matricula`, `nao_cadastrado` ou `identidade_indisponivel`, cada um com a `instrucao_para_o_agente` | `students`, `v_student_course_access` |
| `pesquisar_conteudo_aula` | Pergunta pontual sobre algo dito na aula. Até 5 trechos de **uma** aula | transcrições / chunks |
| `buscar_transcricao_aula` | "Resume a aula", "lista as ferramentas mostradas". Preferindo o `aula_id` do contexto | `lessons` + `modules` + `courses` |
| `pesquisar_perguntas_frequentes` | Dúvidas já respondidas pela equipe, filtráveis por curso e aula. **Só semeada com `faq_credentials` preenchida** | `respostas_duvidas_escola` |

A identidade do aluno entra em todas por `partial`, fora da assinatura visível ao LLM (§2.2). O guardrail nativo `requires_prior_call` amarra `pesquisar_conteudo_aula` e `buscar_transcricao_aula` a uma chamada bem-sucedida de `verificar_matricula_aluno` — é a "ordem obrigatória" do Nexus, feita com a infra do WhatsBot (`vendas_ia/src/agents_seed.py`, `COMERCIAL_HOOKS`).

⚠️ **`buscar_transcricao_aula` precisa de teto de tamanho.** A maior transcrição do acervo tem 73.609 caracteres; devolvê-la inteira estoura o contexto do turno. Corta em N caracteres com aviso explícito de truncamento, e a resposta longa é sempre trabalho de `pesquisar_conteudo_aula`.

### 3.7 Os dois modos de busca (D3)

A primeira CTE é a única diferença; o corpo — full-text português, join, score ponderado, ordenação — é **o mesmo** nos dois, o que garante que a léxica continue sendo um ranking de verdade em vez do ILIKE ingênuo do fallback.

```sql
WITH semantic AS (
  -- hibrida: 1 - (embedding <=> CAST(:vec AS vector)), ORDER BY distância, LIMIT 10
  -- lexica:  fração das palavras da pergunta contidas no texto da aula
),
fulltext AS (
  SELECT …, ts_rank(search_vector, plainto_tsquery('portuguese', :q)) AS ft_score
   WHERE search_vector @@ plainto_tsquery('portuguese', :q)
   LIMIT 10
)
SELECT DISTINCT ON (id)
       curso, modulo, aula, aula_id, trecho,
       COALESCE(sem_score,0) * :peso_sem + COALESCE(ft_score,0) * :peso_ft AS score
  FROM semantic FULL OUTER JOIN fulltext USING (id)
 WHERE course_id = ANY(:cursos_do_aluno)     -- portão de matrícula, sempre
 ORDER BY id, score DESC
 LIMIT :limite;
```

Depois do SQL, o filtro de **uma aula só** (§2.2). Em qualquer falha a busca **degrada, nunca levanta**: híbrida sem vetor cai na léxica; léxica com SQL quebrada cai no casamento por palavra.

- **Modo léxico** roda direto sobre `lessons.video_transcript`, com o trecho recortado em memória na hora. Nenhuma escrita, nenhum custo por pergunta, nenhuma chamada de rede dentro da tool.
- **Modo híbrido** usa `plugin_escola_ia_chunks`, no banco do WhatsBot (D4). O indexador reaproveita o `content_hash` para reprocessar só o que mudou, e o chunking mantém 500/50 (§2.2).

⚠️ **Bind params NOMEADOS** (`:q`), nunca `%s` — convenção do repo. O cast do vetor é `CAST(:vec AS vector)`, não `%s::vector`.

### 3.8 Agente, prompt e roteamento

`agents_seed.py` cria (se ainda não existir) o agente `suporte_escola`, com:

- prompt em `seed_prompts/suporte_escola.md`, adaptado do prompt real do Nexus — a parte que importa é o fluxo por status de matrícula (`python/src/agents/prompts/suporte_prompt.py:12-46`), que já traz as respostas certas para cada desfecho;
- `tool_names` = as tools acima + `transferir_agente` + `transfer_to_human` + `set_custom_attribute`;
- `hooks_config` com os `requires_prior_call` de §3.6.

`filters.py` implementa `filter.agent.resolve`: contato com identidade de aluno e matrícula vigente ⇒ força `target_agent_key`, pulando o roteador. É síncrono e sem race — e economiza o turno de LLM que o ROUTER gasta hoje. Só age quando a IA está no comando; nunca rouba conversa de humano.

`ignored_messages_user` corta a mensagem antes do turno; `ignored_messages_ia` bloqueia o envio em `filter.outbound.text`.

### 3.9 Tabelas do plugin e migrations

Todas no banco do WhatsBot, prefixo `plugin_escola_ia_`:

| Tabela | Papel |
|---|---|
| `plugin_escola_ia_contexto` | Aula em foco por identidade (`identifier`, `curso_*`, `modulo_*`, `aula_*`, `updated_at`) |
| `plugin_escola_ia_chunks` | Só no modo híbrido: `lesson_id`, `course_id`, nomes desnormalizados, `chunk_index`, `chunk_text`, `embedding vector(768)`, `search_vector tsvector`, `content_hash`, `synced_at` |

`002_vector.sql` faz `CREATE EXTENSION IF NOT EXISTS vector` e **tolera falta de permissão**: sem a extensão, o plugin registra o aviso e força o modo léxico, em vez de falhar o boot.

### 3.10 O que **não** é portado

- **O roteador BIA** (ROUTER/COMERCIAL/FECHAMENTO): vendas já é assunto do `vendas_ia`; aqui só existe o agente da Escola.
- **As sessões Agno** (`ai_suporte_sessions`): histórico e memória são os nativos do WhatsBot.
- **A telemetria `gerenciamento_ia_executions`**: o WhatsBot já tem a sua.
- **O sync de embeddings disparado pelo LMS**: o indexador é do plugin; o `EMBEDDING_SYNC_URL` do LMS deixa de ter função (F8).
- **`buscar_acesso_plataforma` e `gerar_deep_link`**: o login já é do plugin `lms_login`.

---

## 4. Fases

Cada fase é utilizável sozinha. Preencha o "Status de execução" antes de avançar.

### F0 — Preparação (fora do código)

- Criar o usuário read-only no Postgres do LMS com os GRANTs de §3.3 e testar de fora.
- Criar o canal *Site (Widget)* no WhatsBot, anotar `widget_token`, definir `hmac_token` e os domínios autorizados (`escola.techify.one`).
- Decidir se o `pgvector` será instalado agora (só necessário para o modo híbrido).

**Status de execução:** ⏳ **PENDENTE — é a única coisa que falta para o plugin sair do papel.** Nada aqui é código; tudo depende de acesso que só a operação tem. Roteiro:

1. **Usuário read-only no LMS.** Os GRANTs de §3.3 mais `student_last_lesson` (o fallback do D9 lê essa tabela). Cole a URL em *Gerenciar Plugins → Escola IA (LMS) → Configurar*; o save testa a conexão E as colunas antes de gravar, então uma credencial que não serve **não entra** (é a mitigação do R1). Enquanto isso não acontece o plugin fica inerte e diz isso no log a cada boot.
2. **Canal *Site (Widget)*.** Criar, anotar o `widget_token`, definir o `hmac_token` e autorizar `escola.techify.one`. São esses dois valores que a página do curso vai usar na F8.
3. **`pgvector`.** *Não precisa decidir agora.* A migration **não** cria a extensão (uma migration que falha derruba o plugin inteiro); quem cria é o `indexer.ensure_vector_schema`, em runtime e tolerante a falha, e só quando alguém escolhe o modo híbrido e clica em *Reindexar*. O modo padrão é o léxico, que não usa vetor nenhum. Verificado em 2026-08-28: `vector 0.7.4` está **disponível** (não instalado) no Postgres do WhatsBot, e o caminho vetorial inteiro foi exercitado no banco de teste.

Sondagem de 2026-08-28 com a credencial que o `lms_login` já usa: conexão OK, **66 de 78 aulas com transcrição**, 42 alunos, 319 matrículas vigentes, maior transcrição com 73.609 caracteres — exatamente os números do §1.

### F1 — Esqueleto, settings e diagnóstico

Manifesto, `settings.py` com os campos de §3.2, `_config.py`, `lms_db.py` com teste de fumaça ao salvar, tela de diagnóstico (`/status`: conexão, contagem de aulas com transcrição, estado das tools, modo de busca vigente).

**Pronto quando:** a tela mostra "conexão OK" e o total de aulas com transcrição (esperado: 66 de 78).
**Status de execução:** ✅ **FEITO.** `plugin.yaml`, `_config.py`, `settings.py` (com o teste de fumaça no `model_validator`, que recusa credencial que não conecta), `lms_db.py` (URL normalizada para `psycopg` v3, engine singleton por slot, `statement_timeout` de 8 s, `connect_timeout` de 5 s), `routes.py` (`/status`, `/test-connection`) e `static/config.js`. Validado contra o banco real em 2026-08-28: `ping` OK, `smoke_test` OK, `counts` = 66/78 aulas com transcrição, 42 alunos, 12 cursos, 319 matrículas.

**Dois desvios do §3.2, com o porquê:**

- **`openrouter_capture_real_cost` mudou de significado.** No Nexus ele injeta `usage.include` na chamada do modelo que CONVERSA. Aqui o modelo que conversa é o do WhatsBot, que já contabiliza o próprio uso — o campo ficaria morto. Ele existe, mas agora significa "registrar no log o consumo informado a cada embedding", que é o único custo que este plugin de fato provoca.
- **`search_mode` tem dois valores, não três.** O `catalogo` do `vendas_ia` ("manda o catálogo inteiro e deixa a IA escolher") não tem análogo aqui: o acervo é transcrição de aula, não uma lista de ofertas — mandar tudo estouraria o turno antes de responder qualquer coisa.

### F2 — Identidade, matrícula e aula em foco

`enrollment.py`, `context.py`, a rota pública `/public/contexto` com verificação de HMAC e rate-limit, o fragmento "AULA EM FOCO" e o fallback por `student_last_lesson`.

**Pronto quando:** uma conversa de teste mostra curso, módulo e aula corretos no prompt montado, e a assinatura inválida é recusada.
**Status de execução:** ✅ **FEITO.** `enrollment.py` (identidade + matrícula + os seis status como decisão PURA), `context.py` (HMAC, saneamento, TTL, fallback, espelho no painel), `prompts.py` (o bloco "AULA EM FOCO") e a rota pública em `routes.py`. Validado contra o banco real: resolução por e-mail e por telefone (últimos 8 dígitos) devolvem o MESMO aluno, e `student_last_lesson` responde com curso/módulo/aula. `test_contexto.py` e `test_rota_publica.py` cobrem assinatura válida/inválida, corpo lixo, TTL e rate-limit.

**Três decisões que o plano não tinha fechado:**

- **Como o plugin liga a conversa ao aluno.** O contato do widget não tem telefone: o `chat_id` é o token de sessão. A ponte é `plugin_website_sessions.identifier` — o e-mail que o `identify` daquele plugin já grava depois de conferir o HMAC. É uma leitura de tabela de OUTRO plugin, então é integralmente defensiva: plugin ausente, tabela inexistente ou coluna renomeada caem no degrau seguinte (e-mail salvo no contato → telefone) em vez de quebrar o turno.
- **O bloco do prompt diz de ONDE veio a aula.** "Está assistindo isto agora" e "foi aqui que ele parou" não são a mesma afirmação; apagar a distinção faria o agente responder com confiança sobre uma aula aberta ontem.
- **Carimbo ausente conta como vencido**, mesmo com o TTL desligado: a ausência de data não é "vale para sempre", é "não sabemos quando isto foi escrito".

### F3 — Tools de conteúdo, modo léxico

`search.py` no modo léxico, `tool_code/` das três primeiras tools, `tools_seed.py`. É aqui que o aluno passa a receber resposta de verdade.

**Pronto quando:** uma pergunta sobre aula com transcrição volta com o trecho certo; aluno sem matrícula recebe a negativa; aula sem transcrição vira handoff sem detalhe técnico.
**Status de execução:** ✅ **FEITO.** `search.py` (léxico + híbrido), `tools_seed.py` e as três tools em `tool_code/`. Validado contra o acervo real em 2026-08-28: *"como acessar as ferramentas"* devolveu 5 trechos da mesma aula (score 0,70) e *"mikrotik configuração"* devolveu 3 — trechos de verdade, não vazio. Uma aula sem transcrição (`Acesso as ferramentas`, 0 caracteres) cai no handoff sem detalhe técnico, como manda §2.2.

**Um desvio estrutural do §3.7, imposto pelo D4.** O plano dizia que "a primeira CTE é a única diferença" entre os dois modos — isso valia quando os dois liam a mesma tabela. Com o D4 (vetores no banco do WhatsBot) eles leem **bancos diferentes**: o léxico vai a `lessons.video_transcript`, no LMS; o híbrido vai a `plugin_escola_ia_chunks`, no WhatsBot. A **forma** foi preservada nos dois (CTE semântica + CTE full-text + `FULL OUTER JOIN` + score ponderado), que é o que garante que a léxica continue sendo um ranking de verdade em vez do ILIKE ingênuo do fallback.

**E uma consequência que valia registrar:** como o modo léxico não tem índice no banco da Escola (nem pode ter — ele é read-only), o `to_tsvector` é calculado na hora. Isso só é aceitável porque o acervo é pequeno (78 aulas) e a consulta já entra filtrada pelos cursos do aluno. Por isso a busca foi partida em DUAS consultas: primeiro ranqueia as aulas **sem trazer transcrição**, depois busca o texto só da aula vencedora e recorta os trechos em memória. Trazer 74 mil caracteres pela rede a cada pergunta seria o caminho óbvio e errado.

### F4 — Agente, prompt e roteamento

`agents_seed.py`, `seed_prompts/suporte_escola.md`, `filter.agent.resolve`, `ignored_messages_*`.

**Pronto quando:** a conversa cai no agente da Escola sem passar por roteador e a ordem "matrícula antes de conteúdo" é respeitada mesmo quando o modelo tenta pular.
**Status de execução:** ✅ **FEITO.** `agents_seed.py` (agente `suporte_escola`, seed não-destrutivo), `seed_prompts/suporte_escola.md` e `filters.py`. A "ordem obrigatória" é exercitada contra o `check_hooks` REAL do core em `test_seed.py`: bloqueia conteúdo sem matrícula, libera depois de uma verificação bem-sucedida e **continua bloqueando depois de uma verificação NEGATIVA**.

⚠️ **Descoberta que mudou o código:** o `requires_prior_call` é *success-aware* e decide lendo o TEXTO do resultado anterior contra `ai_engine/hooks.py::_FAILURE_MARKERS` — que casa `"não encontrad"`, e **não** `"não encontrou"`. Com a redação original do Nexus, uma matrícula verificada e negativa contaria como sucesso e **abriria** as tools de conteúdo justamente no caso em que a porta tem de estar fechada. Por isso todo desfecho negativo agora carrega um campo `resultado` com o texto literal `"matrícula não encontrada"`, e há teste comparando com o `_result_ok` do core para que uma reescrita inocente ("não localizei") não desligue o portão em silêncio.

**Um desvio do §3.8, por impossibilidade técnica.** O plano mandava bloquear `ignored_messages_ia` em `filter.outbound.text`. Esse seam é **transform-only**: `None` ali significa "sem mudança", não "bloqueia" (`plugins/events.py:180-184` e os dois call sites em `messaging_service`), então o campo não teria efeito nenhum. Quem de fato bloqueia é `filter.reply.part` (`None` pula a parte). Com a decisão da usuária (2026-08-28), `ignored_messages_ia` **saiu do escopo**: o `ignored_messages_user` foi implementado (em `filter.llm.messages`, que aborta o turno antes do modelo) e o lado da IA fica com o que o WhatsBot já oferece — o filtro de histórico por regex e o gate do humano. Se um dia voltar, o seam é `filter.reply.part`, distinguindo IA de operador por `extras["source"]`.

### F5 — Dúvidas frequentes (D6)

`faq_db.py`, a rota write-only que guarda a URL (§3.2), `tool_code/pesquisar_perguntas_frequentes.py`, semeada só quando a URL existe. Antes de codificar, confirmar o **formato da tabela** (§6, Q1).

**Pronto quando:** com a URL preenchida a tool aparece e responde; vazia, ela não é semeada e nada quebra.
**Status de execução:** ✅ **FEITO, com a Q1 resolvida por construção.** `faq_db.py`, a rota write-only `GET/PUT /faq-settings`, a seção da tela e a quarta tool. Decisão da usuária (2026-08-28): em vez de esperar a resposta da Q1, o módulo **inspeciona o schema** e se adapta — aceita `duvida`, `pergunta`, `tipo_duvida`, `titulo` ou `questao` como coluna da pergunta (nessa ordem de preferência), detecta se existe `search_vector` e usa o `to_tsvector` na hora quando não existe. Tabela sem coluna reconhecível **desliga a tool com motivo legível** na tela, em vez de mandar SQL quebrada ao agente.

Consequência prática: a Q1 deixou de ser bloqueante — **as duas** formas sondadas funcionam sem código novo, e a Q2 ("o acervo vai continuar sendo alimentado?") também não bloqueia nada, porque com a URL vazia a tool simplesmente não é semeada.

⚠️ **A URL de FAQ NÃO é campo do formulário, e isso é testado.** Se `faq_credentials` virar um campo de `Settings`, dois vazamentos abrem de uma vez: `GET /api/plugins/<id>/settings` devolve os valores do form em claro (`format: password` é inerte), e o sanitizador da trilha casa o NOME da chave por igualdade exata — só conhece `credentials`. `test_conexao.py` trava as duas coisas.

### F6 — Modo híbrido

`002_vector.sql`, `indexer.py`, `embeddings.py` com teto de 3 s, botão "reindexar" na tela, `search_mode` valendo na busca seguinte.

**Pronto quando:** trocar o modo na tela muda o resultado sem restart, e derrubar a rede degrada para o léxico em vez de matar a tool.
**Status de execução:** ✅ **FEITO.** `indexer.py` (chunking 500/50, `content_hash` para reprocessar só o que mudou), `embeddings.py` (teto de 3 s na busca, 30 s na reindexação, que roda fora da tool) e os botões *Reindexar* / *reprocessar tudo* / *Apagar índice* na tela. O caminho vetorial inteiro foi exercitado no banco de TESTE: extensão instalada, coluna `vector(N)`, `INSERT` com `CAST(:vec AS vector)`, a SQL da busca semântica com e sem filtro, e o índice `ivfflat` — depois tudo removido, extensão inclusive.

⚠️ **Não existe `002_vector.sql`, e a ausência é a decisão.** O plano queria que a migration criasse a extensão "tolerando falta de permissão" — mas o migrator roda o arquivo inteiro numa transação só, e uma migration que falha faz o loader gravar `load_error` e **pular o plugin**: sem a extensão, o plugin não carregaria de jeito nenhum, que é o oposto do pedido. Então a coluna `embedding` e a extensão são criadas em **runtime** (`indexer.ensure_vector_schema`), cada passo em transação própria, e a falha vira aviso + modo léxico. De brinde, a dimensão do vetor passa a vir da configuração em vez de ficar cravada no SQL. A migration `001` cria as duas tabelas e a coluna `search_vector` como **`GENERATED ALWAYS AS (to_tsvector('portuguese', chunk_text)) STORED`**, que não pode divergir do texto.

### F7 — Testes e empacotamento

Testes de busca (os dois modos e a degradação), matrícula (os seis status), contexto (HMAC válido/inválido, TTL, fallback), truncamento da transcrição. ZIP e entrada no `catalog.json`.

**Status de execução:** ✅ **FEITO. 228 testes, todos verdes** pelo runner oficial (`python3 scripts/test_plugins.py escola_ia`), DB-free — rodam num clone limpo, sem Postgres e sem o banco da Escola. Oito arquivos: `test_matricula.py`, `test_contexto.py`, `test_busca.py`, `test_indexacao.py`, `test_conexao.py`, `test_roteamento.py`, `test_rota_publica.py` e `test_seed.py`.

Os testes que existem por causa de um modo de falha concreto, e não por cobertura:

- o portão de matrícula comparado contra o `_result_ok` REAL do core (o achado da F4);
- **nenhuma tool expõe identidade na assinatura** — se um `phone`/`email`/`course_ids` aparecer nos parâmetros de um schema, o modelo passa a poder pedir o conteúdo de outro aluno;
- **nenhum `tool_code/*.py` tem instrução executável no topo do módulo** (checado por AST): ele é RE-EXECUTADO in-process quando o operador salva uma edição pela tela;
- **nenhuma migration tem `;` ou apóstrofo dentro de comentário** — o migrator splita por `;` sem remover comentários e o splitter respeita aspas simples;
- o `X-Forwarded-For` da rota pública lido da DIREITA para a esquerda (a parte esquerda é forjável e zeraria o rate-limit);
- `faq_credentials` fora de `Settings`, pelas duas rotas de vazamento.

ZIP gerado e conferido byte a byte (`build_plugins.py --check escola_ia`: 24 arquivos, 74.069 bytes), `escola_ia.json` e entrada no `catalog.json`.

### F8 — Cutover no LMS (PR próprio)

Trocar o widget do Chatwoot pelo do WhatsBot na página do curso, apontar o envio de contexto para a nova rota, desligar o webhook da inbox 6 no Chatwoot. Só depois de validado: remover `ChatwootWidget.tsx`, `useChatwoot.ts`, as rotas `/chatwoot/*` e o `EMBEDDING_SYNC_URL`. O `lms_login` continua como está.

**Pronto quando:** um aluno real conclui uma dúvida ponta a ponta sem o Chatwoot no caminho.
**Status de execução:** ⏳ **PENDENTE — não executada por escolha de escopo (2026-08-28).** É outro repositório (`/opt/lms`) e um PR próprio, e o plano já a separava justamente para o Chatwoot só sair depois de o plugin estar validado (R7).

O lado do WhatsBot **já está pronto para receber**. O que o front do curso precisa fazer, a cada troca de aula:

```js
await fetch('https://<whatsbot>/api/plugins/escola_ia/public/contexto', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    identifier: aluno.email,
    identifier_hash: hmacDoEmail,   // o MESMO que /chatwoot/hash já devolve hoje
    widget_token: WIDGET_TOKEN,     // o do canal Site (Widget), da F0
    contexto: {
      curso:  { id: curso.id,  nome: curso.name  },
      modulo: { id: modulo.id, nome: modulo.name },
      aula:   { id: aula.id,   nome: aula.name   },
    },
  }),
})
```

O `identifier_hash` é `HMAC-SHA256(hmac_token_do_canal, email)` em hex — **exatamente** o mesmo cálculo que o `identify` do plugin `website` usa e que o LMS já produz hoje para o Chatwoot (`/opt/lms/backend/src/index.ts:267`); muda só o segredo (o `hmac_token` do canal, não o do Chatwoot). Há teste travando essa igualdade. A rota responde sempre `200 {"ok": bool}`, sem detalhe: é pública. Assinatura inválida, token desconhecido ou corpo sem aula devolvem `{"ok": false}` e não gravam nada.

---

## 5. Riscos

| # | Risco | Mitigação |
|---|---|---|
| R1 | **Credencial do LMS inválida em silêncio** — foi o que quebrou a solução atual por 49 dias | Teste de fumaça ao salvar (§3.3): credencial que não conecta não é gravada. A tela de diagnóstico mostra o estado a qualquer momento |
| R2 | **Rota pública abusada** — ela aceita dados de qualquer origem | HMAC obrigatório, comparação em tempo constante, rate-limit por identidade, resposta sem detalhe, corpo nunca auditado |
| R3 | **Aluno anônimo no widget** | Sem identidade verificada não há conteúdo: o agente pede o e-mail ou transfere. Nunca adivinha pelo nome |
| R4 | **Transcrição gigante estoura o turno** | Teto de tamanho em `buscar_transcricao_aula` (§3.6) |
| R5 | **Embedding lento mata a tool e multiplica tokens** | Teto de 3 s e degradação para o léxico (§2.3). O modo padrão nem chama a rede |
| R6 | **Aulas sem transcrição (12 de 78)** | Handoff explícito, nunca resposta inventada. A tela de diagnóstico mostra quantas faltam |
| R7 | **Dois widgets no ar durante o cutover** | F8 é fase separada, com PR próprio; o Chatwoot só sai depois de validado |
| R8 | **Seed sobrescrever agente/tool do usuário** | Seed não-destrutivo (só cria o que não existe), igual ao `vendas_ia` |

---

## 6. Perguntas em aberto

**Q1 — Qual é o formato da tabela de dúvidas frequentes?** ✅ **RESOLVIDA por construção (2026-08-28), sem precisar da resposta.** Em vez de escolher entre `duvida` (banco `ia`) e `tipo_duvida` (banco `nexus`), o `faq_db.py` **inspeciona o schema** e aceita as duas — mais `pergunta`, `titulo` e `questao` —, detectando também se existe `search_vector` (e calculando o `to_tsvector` na hora quando não existe). Tabela sem coluna reconhecível desliga a tool com um motivo legível na tela, em vez de mandar SQL quebrada ao agente. Resta apenas a decisão de operação: **qual banco receberá o acervo real**, para colar a URL na tela.

**Q2 — O acervo de dúvidas vai continuar sendo alimentado?** ✅ **Deixou de bloquear.** Com a URL vazia — o estado padrão — a quarta tool simplesmente não é semeada, o agente nasce sem ela e nada quebra. A pergunta continua valendo como decisão de produto, mas agora ela é reversível a qualquer momento: basta preencher (ou limpar) a URL na tela e semear de novo.

**Q3 (nova) — Vale migrar a URL do LMS para a rota write-only também?** Hoje `credentials` é campo do formulário, com o mesmo trade-off que `lms_login` e `vendas_ia` já aceitam: o nome protege a trilha de auditoria, mas `GET /api/plugins/escola_ia/settings` devolve a URL em claro para quem tem `plugins.manage`. A infraestrutura para movê-la já existe (é a mesma rota da URL de FAQ) — é decidir se o incômodo de configurar em dois lugares compensa fechar essa porta.

**Q3 — O `pgvector` pode ser instalado no Postgres do WhatsBot?** A extensão está disponível, mas `CREATE EXTENSION` exige privilégio. Se não puder, o plugin fica só com o modo léxico (que é o padrão de qualquer forma) e a F6 é adiada.

**Q4 — Alguma conversa da inbox "Escola" precisa ser migrada?** O histórico atual vive no Chatwoot. O plano assume que não — o WhatsBot começa do zero para esses alunos.

---

## 7. Referências

**LMS** (`/opt/lms`, branch `main`, `8db74bb`)
- `frontend/src/pages/CoursePage.tsx:78` — contexto da aula no `CourseContext`
- `frontend/src/components/ChatwootWidget.tsx:14` — montagem do `chat_info`
- `frontend/src/hooks/useChatwoot.ts:69`, `:130`, `:139`, `:148` — identidade e publicação do contexto
- `backend/src/index.ts:253`, `:267` — `/chatwoot/config` e `/chatwoot/hash`
- `backend/src/controllers/lastLessonController.ts:59` — consulta de `student_last_lesson`
- `backend/migrations/005_create_last_lesson_table.sql`
- `backend/src/services/embeddingSyncService.ts` — sync fire-and-forget (hoje 404)

**Motor atual** (`~/opt/nexus-tech-gerenciamento-ia`)
- `python/src/webhooks/chatwoot_handler.py:215` — bloco `[Contexto do cliente]`
- `python/src/tools/verificar_matricula.py`, `pesquisar_conteudo_aula.py:127`, `buscar_transcricao.py:56`
- `python/src/services/database.py:298` — aluno + cursos pelos últimos 8 dígitos
- `python/src/services/chunker.py:9` — chunking 500/50
- `python/src/agents/prompts/suporte_prompt.py:12` — fluxo por status de matrícula

**WhatsBot** (`~/opt/whatsbot-pro`, `~/opt/whatsbot-pro-plugins`)
- `agent/tool_isolation.py:54` — teto de 10 s do subprocesso
- `agent/prompt_builder.py:131` — `PROMPT_FRAGMENTS`
- `agent/history_filter.py:41` — `ai_history_exclude_patterns`
- `ai_engine/model_factory.py` — cascata do modelo
- `plugins/website/src/routes.py:4`, `:264`; `src/static/sdk.js:132`; `src/sessions.py`
- `plugins/vendas_ia/src/` — `nexus_db.py`, `search.py`, `embeddings.py:37`, `tools_seed.py:42`, `agents_seed.py`, `prompts.py`, `settings.py`
- `plugins/lms_login/src/settings.py`, `lms.py` — URL única, teste de fumaça, auditoria sem senha
