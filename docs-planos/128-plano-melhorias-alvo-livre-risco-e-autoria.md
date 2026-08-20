# Plano 128 — A melhoria deixa de ser só da IA: alvo livre, níveis de risco na aprovação, pedido avulso, áudio e autoria

> **Status:** PLANEJAMENTO · **Data:** 2026-08-18 · **Escopo:** grande (plugin `melhorias` **1.7.1 → 1.8.0**, migração **005**, **1 mudança no executor** `whatsbot-ai-server`, **zero mudança no core do WhatsBot**)
> **Origem:** pedido do usuário (2026-08-18) — cinco frentes: (1) gerar melhoria a partir de **qualquer** mensagem, não só da resposta da IA; (2) **segmentar por risco** quem pode aprovar o quê; (3) **botão de criar** melhoria avulsa no painel; (4) **áudio** no chat com a IA; (5) **autoria/auditoria** de quem falou e de quem aprovou.
> **Método:** leitura do código real da fonte de dev (`../whatsbot-pro-plugins/plugins/melhorias/src/`), do core (`web/static/js/`, `plugins/`, `db/`), do executor em `<host-do-executor>:/opt/whatsbot-ai-server/src/` (via ssh) e **consultas ao banco de PRODUÇÃO** (todas as medições de §2.5 e §3.4 são reais).
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| **D1** ✅ 2026-08-18 (usuário) | **Dois níveis de risco**: `básico` e `avançado` | Duas chaves RBAC de aprovação em vez de uma. Ver **D1-a** — a classificação é por **efeito**, não por nome de ferramenta |
| **D1-a** ✅ 2026-08-18 (derivada, ver §3.2) | O nível sai do **efeito** da mutação, lido de `tool_input`, não só do `tool_name` | `update_agent` que só troca `prompt`/`description` é **básico**; o mesmo `update_agent` que troca `tool_names`/`is_router`/`model_config` é **avançado**. Sem isso o modelo escapa do nível trocando de ferramenta — `patch_agent_prompt` e `update_agent{prompt}` fazem a MESMA coisa |
| **D2** ✅ 2026-08-18 (usuário) | Permissão **`chat` separada** de aprovar | Conversar com a IA deixa de exigir `approve`. Atendente investiga e propõe; aplicar continua gateado por nível |
| **D3** ✅ 2026-08-18 (usuário) | Mutação acima do nível **fica pendente** para quem tem o nível | Fila de escalonamento persistida. Ver **D3-a** — o executor **não** pode esperar |
| **D3-a** ✅ 2026-08-18 (derivada, ver §2.4) | O escalonamento **não segura o executor**: o gateway responde NA HORA e **reaplica** a mutação sozinho quando o sênior decide | `APPROVAL_TIMEOUT_MS = 5 min` e a fila do executor é **in-memory** ([pending-approvals.ts:17](#)). Segurar a promise por horas é impossível: 5 min ou um restart matam. Exceção: **`db_write` nunca entra na fila** (o core não tem o DSN de escrita — é o desenho do plano 74) |
| **D4** ✅ 2026-08-18 (usuário) | `db_write` exige **chave própria** (`approve_db_write`) além do nível avançado | É a única ferramenta **sem versionamento e sem rollback**, e a única que **passa por fora do RBAC do core** (§2.4). Fica atrás de duas chaves |
| **D5** ✅ 2026-08-18 (usuário) | Alvo elegível = **toda bolha do chat + nota privada**; cards de sistema ficam de fora | Zero mudança no core: são exatamente os papéis que já abrem menu de contexto hoje (§2.1) |
| **D6** ✅ 2026-08-18 (usuário) | Mídia é aceita: **texto derivado + a imagem como anexo** | Áudio/documento entram pela transcrição/descrição; imagem vai também como parte visual (o executor já aceita `image/base64`). Sem texto nem transcrição, entra com marcador em vez de ser recusada |
| **D7** ✅ 2026-08-18 (usuário) | Melhoria avulsa = **texto livre + link opcional** da mensagem | Nada de seletor de conversa novo. O link é o permalink que o core já copia: `<base>/conversations/<id>?message=<msgId>` ([useMessageActions.js:113](../web/static/js/components/contacts/hooks/useMessageActions.js#L113)) |
| **D8** ✅ 2026-08-18 (usuário) | Áudio no chat: **grava no painel, guarda o arquivo E a transcrição** | O áudio fica tocável no histórico (auditoria); a IA recebe a transcrição com o nome de quem falou |
| **D9** ✅ 2026-08-18 (usuário perguntou; ver §2.6) | **Classificar é do gateway, nunca do executor.** O Claude que roda a IA recebe documentação de **consciência** do modelo de risco, não a **lógica** de classificação | O `risk.py` no plugin é a fonte da verdade (fail-closed). O executor ganha uma seção no `system-prompt.ts` + um guia `niveis-de-risco.md` **descritivos**: preferir o nível básico, avisar quando for avançado, entender a resposta de escalonamento. Drift na doc do executor **não** afrouxa o portão (F6·B / F7·7). Ver **P6** |

**Princípios fixos do repo que valem aqui:**
- A **fonte de desenvolvimento** é `../whatsbot-pro-plugins/plugins/melhorias/src/`; `storages/plugins/melhorias/` é a **cópia instalada** (hoje 1.7.0, a publicada é 1.7.1 — `diff -rq` só acusa `plugin.yaml` e os `.test.js`). Editar uma e esquecer a outra é perder o trabalho (memória `plugin-instalar-local-antes-de-publicar`).
- Migração de plugin: prefixo `plugin_melhorias_` obrigatório, **nenhum `;` dentro de comentário** (o migrator quebra o SQL no `;` **antes** de remover comentários — [plugins/migrator.py](../plugins/migrator.py)), `INTEGER PRIMARY KEY AUTOINCREMENT` vira `SERIAL`.
- Import do core dentro do plugin é **defensivo** (`try/except`) — `db.repositories`, `plugins.context` e companhia **não são API declarada** (CLAUDE.md §"'Não muda o core' ≠ 'não depende do core'").
- Tela de plugin **nunca** abre `new WebSocket('/ws')` — o transporte é `api.services.subscribe` / `wsBus` (o plugin já faz certo, [chat.js:40](#)).
- Modo escuro: classes `wa-*` e `.wa-field`; nada de hex inline novo.

---

## 1. Resumo executivo

O plugin `melhorias` hoje só sabe olhar para **uma resposta da IA**, e a única pergunta que ele faz sobre permissão é *"você pode aprovar?"* — um booleano. Quem responde "sim" pode, na mesma tela, mandar a IA reescrever o prompt da BIA, criar uma tool de código Python e rodar um `DELETE` no banco de produção.

Este plano faz quatro coisas, todas dentro do plugin (mais **um** interruptor no executor):

1. **Alvo livre** — o filtro do menu de contexto deixa de exigir `role === 'assistant'` e passa a aceitar toda bolha do chat + nota privada, com ou sem mídia. O contexto que vai para a IA deixa de dizer *"resposta marcada como incorreta"* e passa a rotular quem escreveu.
2. **Risco em dois níveis** — um módulo puro (`risk.py`) classifica cada mutação proposta pelo **efeito** que ela tem, e a decisão ✓/✕ passa a exigir a chave daquele nível. Acima do nível, vira **fila**: o gateway responde ao executor na hora e **reaplica** a mutação sozinho quando alguém com a chave decidir.
3. **Pedido avulso e áudio** — botão "Nova melhoria" no painel (texto livre + link opcional) e microfone no chat (grava, transcreve no servidor, manda o texto e guarda o áudio).
4. **Autoria e trilha** — o chat passa a mostrar **quem** mandou cada mensagem, e toda mutação aplicada passa a gravar linha em `/audit` (hoje **nenhuma** grava — §2.7).

O trabalho maior é o **motor de risco + fila** (F3/F6/F7); as outras frentes são independentes e podem correr em paralelo.

---

## 2. Como funciona hoje (mapa)

### 2.1 O gate do alvo é **uma linha** no frontend do plugin

O seam do core (`filter.message.contextMenu.items`) roda em **toda** mensagem, sem discriminar papel — [ContactDetail.js:829](../web/static/js/components/contacts/ContactDetail.js#L829) monta os itens base e entrega ao filtro com `{message, isFromMe, phone, conversationId, sandbox}`. Quem estreita é o plugin:

```js
// static/extends.js:42
const isAiReply = m.role === 'assistant' && m.status !== 'operator'
  && !m.revoked && !ctx.sandbox;
if (!isAiReply || !can('request')) return items;
```

O mesmo gate se repete na ação em lote ([extends.js:59](#)).

**Quais papéis já abrem menu de contexto hoje** (é o que define o teto de D5 sem tocar no core):

| Render | Papéis | Tem `onContextMenu`? |
|---|---|---|
| [MessageBubble.js:70](../web/static/js/components/contacts/MessageBubble.js#L70) | `user`, `assistant` (IA), `assistant`+`status='operator'` (atendente/automação/template) | ✅ sim |
| [SystemMessageCard.js:59](../web/static/js/components/contacts/SystemMessageCard.js#L59) | `private_note` | ✅ sim |
| [SystemMessageCard.js](../web/static/js/components/contacts/SystemMessageCard.js) (demais ramos) | `transcription`, `tool_call`, `system_notice`, `conversation_event`, `system`, `error` | ❌ **não** — nenhum handler |

⚠️ É por isso que D5 é gratuita e a alternativa "tudo, inclusive cards de sistema" não era: os cards de sistema **não têm menu**, e dar menu a eles é mudança de core.

### 2.2 O servidor recusa mídia e assume que o alvo é a IA

Três amarras no caminho de criação:

| # | Onde | O quê |
|---|---|---|
| 1 | [logic.py:218](#) e [logic.py:224](#) | `targets` só aceita item com `content` não-vazio ⇒ **imagem sem legenda, áudio e documento são recusados** com `"Mensagem inválida para análise."` |
| 2 | [logic.py:244](#) | O caminho *single* grava só `{content, ts, _id}`; `media_type`/`media_path` só chegam pela multi-seleção ([extends.js:88-92](#)). A tabela filha **já tem as colunas** ([002_multi_message.sql](#)) — falta o produtor |
| 3 | [logic.py:228-230](#) | `contact_repo.get_by_phone(phone)` obrigatório ⇒ **não existe sugestão sem contato** (`contact_id INTEGER NOT NULL` em [001_initial.sql](#)) |

E o texto que vai para a IA é escrito para uma resposta de bot:

```python
# generation.py:286
if role == "assistant" and content in unmarked:
    marker = "   ⟵ RESPOSTA MARCADA COMO INCORRETA"
# generation.py:295
targets_section = f"## Resposta marcada como incorreta\n{target_contents[0]}"
```

Marcar uma mensagem do **cliente** hoje produziria um prompt em que o alvo nunca aparece marcado no histórico e o cabeçalho mente sobre a autoria — a IA diagnosticaria como se ela mesma tivesse escrito.

### 2.3 Permissão é um booleano, e ele gateia coisas de peso muito diferente

O manifesto declara 4 chaves ([plugin.yaml](#)): `request`, `view`, `approve`, `configure`. A `approve` sozinha gateia **nove** rotas ([routes.py](#)):

| Rota | Linha | O que faz |
|---|---|---|
| `POST /suggestions/{sid}/approve` | 113 | abre a conversa agêntica |
| `POST /suggestions/{sid}/reject` | 137 | recusa o pedido |
| `POST /suggestions/{sid}/conversations` | 152 | abre a conversa (gate D1-a do plano 51) |
| `POST /conversations/{cid}/messages` | 189 | **conversar com a IA** |
| `POST /conversations/{cid}/approve` | 236 | **✓/✕ de CADA mutação** |
| `POST /conversations/{cid}/cancel` | 261 | cancela |
| `POST /conversations/{cid}/complete` | 277 | encerra |
| `POST /conversations/{cid}/resume` | 296 | retoma |
| `PUT /default-filter` | 415 | filtro padrão do painel |

Ou seja: **conversar** e **aplicar um `DELETE` no banco** estão atrás da MESMA chave. É exatamente o que D2+D1 desfazem.

### 2.4 O que a IA pode fazer — e o buraco do `db_write`

O executor (`<host-do-executor>:/opt/whatsbot-ai-server`) expõe **11 ferramentas de mutação**, todas bloqueando em `waitForApproval` ([tool-registry.ts:546](#)):

| Ferramenta | Endpoint no gateway | Versionado? | RBAC do core reaplicado? |
|---|---|---|---|
| `patch_agent_prompt` | `POST /_internal/agents/{key}/prompt` ([internal_routes.py:296](#)) | ✅ | ✅ `agent.prompts.edit` |
| `update_agent` | `POST /_internal/agents/{key}` ([internal_routes.py:247](#)) | ✅ | ✅ `agent.config.manage` (+`agent.prompts.edit` se vier `prompt`) |
| `create_agent` | idem (row inexistente) | ✅ | ✅ `agent.create` |
| `set_variable` | `POST /_internal/variables/{name}` ([internal_routes.py:398](#)) | ✅ | ✅ `agent.variables.manage` |
| `create_tool` / `update_tool_code` | `POST /_internal/tools/{name}` ([internal_routes.py:340](#)) | ✅ | ✅ `agent.tools.manage` |
| `set_tool_override` | `POST /_internal/tool-overrides/{name}` ([internal_routes.py:377](#)) | ❌ (gap conhecido) | ✅ `agent.tools.manage` |
| `rollback_agent` / `rollback_tool` / `rollback_variable` | `.../rollback/{version}` | ✅ | ✅ |
| **`db_write`** | **nenhum** — conexão `pg.Client` direta com `WHATSBOT_RW_DSN` ([tool-registry.ts:481](#)) | ❌ **nunca** | ❌ **nunca** |

⚠️ **`db_write` é a exceção em tudo**: INSERT/UPDATE/DELETE arbitrário, um comando por chamada, `WHERE` obrigatório, teto de `DB_WRITE_MAX_ROWS` linhas — e **é só isso**. Não passa pelo `internal_routes`, logo o `authz.acheck` on-behalf-of nunca roda; não é versionado; o próprio texto da ferramenta avisa *"NAO ha rollback automatico"*. Daí **D4**.

⚠️ **A fila do executor é in-memory e expira em 5 minutos**:

```ts
// env.ts:23
APPROVAL_TIMEOUT_MS: z.coerce.number().int().positive().default(5 * 60 * 1000),
// pending-approvals.ts:17
const pending = new Map<string, PendingApproval>();
```

Um restart do serviço ou 5 minutos de espera **rejeitam a promise**. É o fato que força **D3-a**: "fica pendente" não pode significar "o executor espera".

⚠️ **O `on_behalf_of` é de quem ABRIU a conversa, não de quem aprova.** O runner carrega `params.userId` fixo desde o `start` ([conversation-runner.ts:128](#)) e toda tool chama `ctx.client.post(..., ctx.userId)`. Se a Erika abre a análise e o Thiago clica ✓, a mutação é gravada **em nome da Erika**. A fila de D3-a conserta isso no caminho dela (a reaplicação é on-behalf-of quem decidiu); o caminho direto continua com o comportamento atual — ver **P2**.

⚠️ **Bash livre no host do executor.** O runner roda com `permissionMode: 'bypassPermissions'` e `allowDangerouslySkipPermissions: true` ([conversation-runner.ts:196](#)), com Bash/Read/Edit habilitados e um DSN **somente-leitura** no shell. É desenho aceito do plano 74 (o segredo de escrita mora só na tool `db_write`), mas define a fronteira honesta deste plano: **os níveis de risco governam as MUTAÇÕES, não tudo que o agente consegue fazer na máquina dele.** Ver R6.

### 2.5 Uso real em produção (medido em 2026-08-18)

| Medição | Valor |
|---|---|
| Sugestões | 30 — `em_chat` **23**, `concluida` 3, `aprovada` 2, `pendente` 1, `recusada` 1 |
| Solicitantes distintos | Erika, Thiago, Gabriel Vargas, Suporte, Automação |
| Aprovações de mutação | **19** — `patch_agent_prompt` 15, `update_agent` 4 |
| Recusadas | **0 de 19** |
| Quem decidiu | user 6 (Erika) e user 12 (Thiago) — ambos Administrador |
| Cargo **Atendente** | tem `plugin.melhorias.request` + `view` … **e `agent.prompts.edit`, `agent.prompts.version`, `agent.variables.manage`** |
| Cargo **Gestor** | tem o pacote `agent.*` completo, **nenhuma** chave de `melhorias`, e **zero usuários** |

Duas leituras que orientam o plano:

1. **A segunda camada não seguraria um atendente.** Se hoje alguém der `plugin.melhorias.approve` a um Atendente, o `internal_routes` **não** barra a troca de prompt — o cargo já tem `agent.prompts.edit`. O nível de risco precisa ser um gate **próprio**, não uma aposta no RBAC do core.
2. **Ninguém nunca recusou nada.** 19/19 aprovadas. O ✓ virou reflexo — mais um motivo para o cartão exibir o nível e o `db_write` pedir uma segunda chave.

### 2.6 O executor não classifica — e por que isso é de propósito (D9)

O Claude que roda a IA (`<host-do-executor>:/opt/whatsbot-ai-server`) **propõe** mutações chamando as 11 ferramentas; **quem julga o risco é o gateway** (`internal_routes` → `risk.py`), sobre `tool_name` + `tool_input`, de forma determinística e fail-closed. Isso é uma escolha de segurança, não um acaso:

- a conversa de melhoria é um canal por onde **texto humano entra no raciocínio do modelo** — pedir que ele se auto-classifique reabriria o mesmo furo de sempre (o operador o convence de que "isso é simples", ou uma instrução no meio da conversa o faz rebaixar);
- o modelo **não vê** o RBAC de quem está do outro lado; o nível só faz sentido no gateway, que sabe.

Mas o executor **precisa saber que o modelo de risco existe** — para se comportar bem em volta do portão, não para operá-lo. Hoje o `system-prompt.ts` já pende nessa direção (*"prefira `patch_agent_prompt` quando a mudança é só de prompt"*), mas **nada** ali fala de níveis, de escalonamento ou de que uma mutação avançada pode ir para uma fila. Três coisas que ele deve entender (F6·B e F7·7):

| O executor deve… | Por quê |
|---|---|
| preferir o nível **básico** quando resolve | um atendente aprova sem escalar → menos atrito |
| **avisar** o operador quando for **avançado**/`db_write` | não prometer efeito imediato numa coisa que vai para a fila |
| ler `approved=false, reason="Encaminhado…"` como **roteamento, não recusa** | senão ele reformula e re-propõe em loop |

⚠️ **A doc do executor é DESCRITIVA; o `risk.py` é AUTORITATIVO.** Se as duas divergirem (a doc dizendo básico onde o `risk.py` diz avançado), o portão ainda gateia certo — o pior caso é o executor prometer "sai rápido" e a coisa cair na fila. Nunca o contrário. Onde manter a fonte única é a **P6**.

Onde a doc vive no servidor (mapeado por ssh em 2026-08-18):

| Lugar | Como carrega | Conteúdo |
|---|---|---|
| `src/core/system-prompt.ts` | sempre (apendado ao preset `claude_code`) | seção curta de consciência + semântica do "Encaminhado" |
| `src/guides/niveis-de-risco.md` *(novo)* | sob demanda via a tool `read_guide()` | a tabela §3.2 como **referência** |
| `/home/whatsbot-ai/work/CLAUDE.md` + `docs/` | `settingSources: ['project']` | ponteiro opcional; o principal é o system-prompt |

Nada disso exige acesso ao banco — é prosa/código no repo do executor (local no servidor, sem remote; commit lá mesmo).

### 2.7 Autoria e auditoria: os dois estão vazios

- `plugin_melhorias_ai_messages` ([003_ai_chat.sql](#)) tem `role, content, tool_name, tool_input, tool_result, token_usage, created_at` — **nenhuma coluna de autor**. O `UserCard` ([chat.js:61](#)) desenha um balão anônimo. Com dois operadores no mesmo chat, não há como saber quem pediu o quê.
- `plugin_melhorias_ai_approvals` guarda `decided_by` (id) mas **não** o nome; o painel não mostra nenhum dos dois.
- **`grep -rn "audit" src/` no plugin: zero ocorrências.** E `ai.config.changed` **não está** em `AUDITABLE_EVENTS` ([db/audit_actions.py:80-104](../db/audit_actions.py#L80-L104)). Logo: **trocar o prompt da BIA pelo chat de melhoria não deixa nenhuma linha em `/audit` hoje.** (A memória do projeto que diz que `melhorias` já audita está desatualizada.)

---

## 3. Inventário — o que muda

### 3.1 Frente por frente

| # | Frente | Onde | O que falta | Risco | Esforço |
|---|---|---|---|---|---|
| A1 | Alvo livre no menu | [extends.js:42](#), [extends.js:59](#) | trocar `isAiReply` por `isEligibleTarget` (papel na allowlist, não revogada, não sandbox) | baixo | S |
| A2 | Alvo livre no servidor | [logic.py:218-224](#) | aceitar alvo **sem texto** quando há `media_type`; gravar `media_type`/`media_path` também no caminho single ([logic.py:244](#)) | baixo | S |
| A3 | Contexto neutro para a IA | [generation.py:280-300](#) | rótulo por papel (`Cliente` / `IA` / `Atendente <nome>` / `Nota privada`), marcador que casa por `_id` e não por `role+content`, cabeçalho `## Mensagem marcada` | **médio** | M |
| A4 | Mídia no contexto | [generation.py:160](#), [chat_logic.py:441](#) | derivar texto (legenda → transcrição/descrição já gravada → transcrever sob demanda) e anexar a imagem como `parts` na 1ª mensagem | médio | M |
| B1 | Motor de risco | **novo** `risk.py` | tabela efeito→nível, puro, sem DB nem rede | baixo | M |
| B2 | Chaves RBAC | [plugin.yaml](#) | `chat`, `approve_advanced`, `approve_db_write` (a `approve` **vira o nível básico**) | baixo | S |
| B3 | Regate das rotas | [routes.py:113-307](#) | 6 rotas passam de `approve` → `chat`; a de decisão passa a checar nível | **médio** | M |
| B4 | Fila de escalonamento | [chat_logic.py:411](#) + novo `replay.py` | persistir a mutação recusada por nível e **reaplicar** quando o sênior decidir | **alto** | L |
| C1 | Melhoria avulsa | [logic.py:206](#), [panel.js](#) | `contact_id` nullable, caminho sem contato, parser de permalink, botão + modal | médio | M |
| C2 | Áudio no chat | novo `POST /conversations/{cid}/audio`, [chat.js:431](#) | gravar (opus-recorder já global), transcrever, persistir arquivo + texto | médio | M |
| D1 | Autoria | migração 005, [chat.js:61](#) | `author_user_id`/`author_name` em `_ai_messages`, `decided_by_name` em `_ai_approvals`, nome no balão e no cartão | baixo | S |
| D2 | Trilha de auditoria | novo em `chat_logic`/`replay` | `plugins.context.audit(...)` por mutação aplicada / recusada / escalonada | baixo | M |
| E1 | Interruptor `db_write` | executor `tool-registry.ts` + `env.ts` | `WBAI_DB_WRITE_ENABLED` para desligar a ferramenta sem rebuild de lógica | baixo | S |

### 3.2 A tabela de risco (D1-a) — por **efeito**, não por nome

O ponto que fecha o bypass: `patch_agent_prompt` e `update_agent{prompt: …}` produzem **a mesma escrita** (os dois caem em `agent_repo.save` — [internal_routes.py:281](#) e [internal_routes.py:311](#)). Classificar por nome poria um em cada nível e o modelo escolheria o mais barato.

| Efeito | Como é detectado (`tool_name` + chaves de `tool_input`) | Nível |
|---|---|---|
| Texto de instrução muda | `patch_agent_prompt`; `update_agent` cujas chaves ⊆ `{prompt, description, display_name, change_note}` **e o agente já existe** | **básico** |
| Valor de variável muda | `set_variable` | **básico** |
| Rótulo/descrição/ligar-desligar de tool registrada | `set_tool_override` | **básico** |
| Voltar para uma versão anterior | `rollback_agent`, `rollback_tool`, `rollback_variable` | **básico** |
| Estrutura do agente muda | `create_agent`; `update_agent` que toque `tool_names`, `routing_targets`, `is_router`, `model_config`, `hooks_config` ou `enabled` | **avançado** |
| Código Python novo/alterado | `create_tool`, `update_tool_code` | **avançado** |
| SQL de escrita | `db_write` | **avançado** + chave `approve_db_write` |
| **Qualquer nome desconhecido** | fallback | **avançado** (fail-closed) |

Regras do motor:
- **Puro**: `tier_for(tool_name, tool_input, *, agent_exists) -> "basico" | "avancado"`. Sem DB, sem rede — testável com `pytest` sem banco.
- **Fail-closed**: nome fora da tabela ⇒ avançado. Uma ferramenta nova do executor nunca nasce liberada.
- **Calculado duas vezes**: no `register_approval` (persistido em `risk_tier`, para exibir/filtrar) e **de novo** no `decide_approval` a partir do `tool_input` gravado. Divergência ⇒ vale **o mais alto**. Isso protege contra a tabela mudar entre o registro e a decisão.

### 3.3 Alvos elegíveis (D5), na forma que o código vai checar

```
ELEGÍVEL   role ∈ {user, assistant, private_note}   ∧ !revoked ∧ !sandbox
           (inclui assistant com status='operator' — atendente, automação, template)
NÃO        role ∈ {system, system_notice, conversation_event, tool_call, transcription, error}
```

⚠️ A lista de exclusão é **redundante por construção** (esses papéis não abrem menu), mas fica escrita no filtro **e** validada no servidor: o `POST /suggestions` é uma rota pública da API, e um cliente pode mandar o que quiser.

### 3.4 Volume — a fila e o áudio cabem folgado

Base: 30 sugestões e 19 aprovações em **34 dias** (~0,9 sugestão/dia, ~0,6 aprovação/dia). Mesmo com o uso decuplicando por causa da abertura do alvo:

| Item | Estimativa/ano | Observação |
|---|---|---|
| Linhas em `_escalations` | < 500 | só o que passa do nível |
| Mensagens de chat | < 20 mil | tabela já existe |
| Áudios gravados | < 3 mil × ~40 KB ≈ **120 MB/ano** | OGG/Opus mono 48 kHz; ver R5 (poda) |

### 3.5 Falsos positivos descartados

| Suspeita | Por que **não** é problema |
|---|---|
| "O core precisa abrir o menu de contexto nos cards de sistema" | D5 exclui esses papéis de propósito. Sem isso, seria mudança de core para ganhar alvos que ninguém pediu (`tool_call`, `conversation_event`) |
| "Precisa de um seam novo no core para o alvo livre" | `filter.message.contextMenu.items` já entrega **toda** mensagem ([ContactDetail.js:829](#)). O estreitamento é 100% do plugin |
| "O `internal_routes` já protege por RBAC, então o nível de risco é redundante" | Falso em produção: o cargo Atendente **já tem** `agent.prompts.edit` e `agent.variables.manage` (§2.5). E `db_write` não passa por lá |
| "Dá para segurar a aprovação no executor até o sênior decidir" | `APPROVAL_TIMEOUT_MS = 5 min` + `Map` in-memory (§2.4). Aumentar o timeout só troca o modo de falhar (restart continua matando, e prende um runner do Claude por horas) |
| "Áudio pode ir direto para a IA" | O executor é Claude Code: aceita `text` e `image/base64`. **Não há entrada de áudio.** Transcrever antes não é atalho, é o único caminho |
| "`ai.config.changed` já auditava as mudanças" | O evento existe e é emitido ([internal_routes.py:437](#)), mas **não está** em `AUDITABLE_EVENTS` — nenhuma linha é gravada |
| "Basta bumpar `WHATSBOT_API_VERSION`" | Nenhum catálogo do bus, símbolo de `plugins.context` ou contrato de manifest muda. A versão **não** anda neste plano |

---

## 4. A migração 005

Arquivo: `src/migrations/005_risco_autoria_audio.sql`. Sem `;` em comentário, tudo com prefixo `plugin_melhorias_`.

| Tabela | Coluna | Tipo | Para quê |
|---|---|---|---|
| `plugin_melhorias_ai_messages` | `author_user_id` | INTEGER | quem mandou (D1/§2.7) |
| | `author_name` | TEXT DEFAULT '' | snapshot do nome (sobrevive a exclusão do usuário) |
| | `audio_path` | TEXT | áudio ditado, relativo a `statics/` (D8) |
| `plugin_melhorias_ai_approvals` | `risk_tier` | TEXT | nível calculado no registro (exibição/filtro) |
| | `decided_by_name` | TEXT DEFAULT '' | snapshot de quem decidiu |
| | `escalated_at` | DOUBLE PRECISION | quando foi para a fila |
| `plugin_melhorias_suggestions` | `contact_id` | **DROP NOT NULL** | melhoria avulsa (D7) |
| | `kind` | TEXT DEFAULT 'message' | `message` \| `standalone` — o painel distingue sem adivinhar por `contact_id IS NULL` |
| **nova** `plugin_melhorias_escalations` | `id` SERIAL, `approval_id` TEXT, `conversation_id` TEXT, `suggestion_id` INTEGER, `tool_name` TEXT, `tool_input` TEXT, `summary` TEXT, `risk_tier` TEXT, `requested_by` INTEGER, `requested_by_name` TEXT, `status` TEXT ('pendente'/'aplicada'/'recusada'/'falhou'), `decided_by` INTEGER, `decided_by_name` TEXT, `decided_at` DOUBLE PRECISION, `applied_at` DOUBLE PRECISION, `error` TEXT, `created_at` DOUBLE PRECISION | a fila de D3-a |

⚠️ Índice em `plugin_melhorias_escalations (status, created_at)` — a fila é lida por status a cada abertura do painel.

⚠️ Rows legadas: `risk_tier IS NULL` nas 19 aprovações já decididas. O painel exibe `—`; nada é recalculado retroativamente (elas já foram aplicadas).

---

## 5. A fila de escalonamento (D3-a), em detalhe

O ponto mais delicado do plano. Sequência quando **Atendente** (só `approve` = básico) recebe um cartão `create_tool` (avançado):

```
1. executor  → POST /_internal/approvals   (register_approval)
                 risk.tier_for(...) = "avancado"  →  grava risk_tier
2. painel    → mostra o cartão com selo "AVANÇADO — precisa de aprovação sênior"
                 os botões ✓/✕ ficam desabilitados para quem não tem a chave
3. atendente → clica "Encaminhar para aprovação"
4. gateway   → POST /conversations/{cid}/approve  {escalate: true}
                 · grava linha em _escalations (status=pendente)
                 · grava approvals.escalated_at
                 · responde ao EXECUTOR:  approved=false,
                     reason="Encaminhado para aprovação avançada — não repita nesta conversa."
                 · a IA segue a conversa normalmente (não fica pendurada 5 min)
5. sênior    → abre o painel, aba "Fila de aprovação", clica ✓
6. gateway   → replay.apply(escalation)  — chama o MESMO endpoint _internal
                 que o executor chamaria, on-behalf-of o SÊNIOR
                 · sucesso  → status=aplicada,  audit(...)
                 · falha    → status=falhou, error=<msg>, aparece na fila em vermelho
```

**Por que reaplicar em vez de esperar:** §2.4. O mapeamento ferramenta → endpoint já é determinístico e está escrito no executor ([tool-registry.ts:213-385](#)); `replay.py` reimplementa **só a tabela**, chamando o router interno **em processo** (nada de HTTP com HMAC contra si mesmo — o `internal_routes` é um `APIRouter` importável, e as funções de mutação podem ser fatoradas para um módulo chamável pelos dois).

**A exceção `db_write`** (D4): não existe endpoint no gateway e o core **não tem** o DSN de escrita. Um `db_write` acima do nível é **recusado, não enfileirado**, com a mensagem *"Escrita direta no banco só pode ser aprovada por quem tem a chave, dentro da própria conversa."*

⚠️ **Reaplicação não é retroativa ao raciocínio da IA.** Quando o sênior aprova horas depois, o mundo pode ter mudado (o prompt já foi editado por outra via, a tool já existe). Por isso `replay.apply` **relê o estado atual** antes de escrever e recusa quando o alvo mudou desde o registro — o operador vê "o agente foi alterado depois do pedido; reabra a análise". Ver **P1**.

---

## 6. Fases / Roadmap

```
WAVE 0   F0 ─ caracterização (🔴 bloqueia tudo)
         F1 ─ migração 005 · F2 ─ alvo livre (front+back) · F3 ─ risk.py (puro)
              └── as três em PARALELO, nenhuma depende da outra
                  (F2 e F3 não tocam banco; F1 não toca lógica)

WAVE 1   F4 ─ RBAC + regate das rotas   [depende de: F3]
         F5 ─ contexto neutro + mídia    [depende de: F2]
         F6 ─ interruptor db_write no executor  [independente, OUTRO repo]

WAVE 2   F7 ─ fila de escalonamento + replay  [depende de: F1,F3,F4]  🔴
         F8 ─ autoria no chat               [depende de: F1]  🟢
         F9 ─ áudio no chat                 [depende de: F1]  🟢
         F10 ─ melhoria avulsa              [depende de: F1,F5]  🟢

WAVE 3   F11 ─ auditoria    [depende de: F7]
         F12 ─ painel: selo de risco, fila, botão Nova melhoria  [depende de: F7,F10]

WAVE 4   F13 ─ testes, build, publicação e instalação local  🔴
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | caracterização | 🔴 | baixo | Suíte atual verde + testes-âncora do comportamento de hoje |
| 0 | **F1** | DB | 🟢 | baixo | `005` sobe e desce limpa no Postgres de teste |
| 0 | **F2** | alvo livre | 🟢 | baixo | Menu aparece em mensagem do cliente; `POST /suggestions` aceita mídia |
| 0 | **F3** | risco | 🟢 | baixo | `risk.py` com tabela de §3.2 e testes puros |
| 1 | **F4** | RBAC | 🔴 | médio | Atendente com `chat` conversa; ✓ de nível alto dá 403 |
| 1 | **F5** | contexto | 🟢 | médio | Prompt inicial rotula autor; imagem sobe como anexo |
| 1 | **F6** | executor | 🟢 | baixo | `db_write` desligável + executor avisa que mudança é avançada |
| 2 | **F7** | fila | 🔴 | **alto** | Encaminhar → sênior aprova → mutação aplicada e versionada |
| 2 | **F8** | autoria | 🟢 | baixo | Balão mostra o nome de quem falou |
| 2 | **F9** | áudio | 🟢 | médio | Grava, transcreve, IA responde ao ditado, áudio toca no histórico |
| 2 | **F10** | avulsa | 🟢 | médio | "Nova melhoria" sem contato abre chat que funciona |
| 3 | **F11** | auditoria | 🟢 | baixo | Cada mutação aplicada vira linha em `/audit` |
| 3 | **F12** | painel | 🟢 | baixo | Selo de risco na lista/cartão + aba Fila + botão Nova |
| 4 | **F13** | release | 🔴 | médio | `build_plugins.py --check` limpo, zip instalado, suíte verde |

---

### F0 — Caracterização: fixar o comportamento de hoje antes de mexer 🔴

**Objetivo:** ter rede antes de tocar em `create_suggestion`, `generation` e nas rotas.

**Itens** `[sequencial]`
1. Rodar a suíte do plugin: `cd ../whatsbot-pro-plugins && python3 scripts/test_plugins.py melhorias`. Registrar o resultado — as falhas pré-existentes conhecidas do core (memória `tres-falhas-pre-existentes-da-suite-do-core`) não valem aqui, mas o plugin pode ter as suas.
2. Rodar `node --test` nos módulos puros do plugin (`tests/js/chat_core.test.js`, `markdown.test.js`).
3. Acrescentar testes-âncora em `tests/python/test_suggestions.py` que **fixam o que existe hoje**: (a) alvo `assistant` cria sugestão; (b) alvo com `content` vazio é recusado; (c) `POST /conversations/{cid}/messages` exige `approve`; (d) `build_analysis_payload` emite `## Resposta marcada como incorreta`.
4. Marcar (b) e (d) como "vai mudar em F2/F5" — o teste **muda de expectativa**, não some.

**Pronto quando:** os 4 âncoras passam contra o código atual e o resultado da suíte está anotado no Status abaixo.

#### Status de execução — Fase 0
**Estado:** ✅ Concluída
- **O que foi feito:** Baseline registrado (57 py + 35 js verdes). Testes-âncora F2 adicionados em `tests/python/test_suggestions.py` já refletindo o comportamento-alvo (mídia aceita, papel validado, nota privada).
- **Como foi feito / decisões:** Como F0 e F2 foram executadas juntas, os âncoras foram escritos direto no comportamento novo (o valor de regressão é o mesmo). Descoberto: o banco de teste compartilhado `whatsbot_test` sofre corrupção por pytest concorrente de outra máquina da rede — criado banco isolado `whatsbot_test_plano128` (UTF8/template0) para todo o trabalho deste plano.
- **Problemas / pendências:** Concorrência no `whatsbot_test`: usar SEMPRE `WHATSBOT_TEST_DB_URL=...whatsbot_test_plano128` ao rodar a suíte deste plano.
- **Verificação:** `python3 scripts/test_plugins.py melhorias` = 76 passed / 35 js ok no banco isolado.

---

### F1 — Migração 005 🟢

**Objetivo:** todo o schema novo numa migração só, sem lógica junto.

**Itens** `[paralelo]`
1. Criar `src/migrations/005_risco_autoria_audio.sql` com a tabela de §4. **Nenhum `;` dentro de comentário.**
2. `ALTER TABLE plugin_melhorias_suggestions ALTER COLUMN contact_id DROP NOT NULL` — Postgres aceita direto (sem batch-mode).
3. `kind TEXT NOT NULL DEFAULT 'message'` — rows existentes nascem `message` pelo default, sem UPDATE.
4. Índice `plugin_melhorias_escal_status_idx (status, created_at)`.
5. Conferir com `plugins/migrator.py` que todos os nomes têm o prefixo.

**Pronto quando:** subir o servidor de dev aplica a 005 sem erro; `\d+ plugin_melhorias_escalations` mostra as colunas; a suíte de F0 continua verde.

#### Status de execução — Fase 1
**Estado:** ✅ Concluída
- **O que foi feito:** Criado `src/migrations/005_risco_autoria_audio.sql`: colunas em `_ai_messages` (author_user_id/author_name/audio_path), `_ai_approvals` (risk_tier/decided_by_name/escalated_at), `_suggestions` (contact_id DROP NOT NULL, kind DEFAULT 'message') + nova tabela `_escalations` + 2 índices.
- **Como foi feito / decisões:** Sem `;` em comentário; todos os nomes com prefixo `plugin_melhorias_` (validado contra `_TABLE_OP_RE` do migrator). `ALTER COLUMN ... DROP NOT NULL` direto (Postgres). `kind` NOT NULL DEFAULT 'message' → rows existentes nascem 'message' sem UPDATE.
- **Problemas / pendências:** Nenhuma.
- **Verificação:** A suíte aplica 001→005 e `plugin_melhorias_escalations` existe (confirmado via SQLAlchemy). 76 passed.

---

### F2 — Alvo livre: menu e criação 🟢

**Objetivo:** qualquer bolha + nota privada vira melhoria; mídia deixa de ser recusada.

**Itens**
1. `[paralelo]` [extends.js:42](#) e [extends.js:59](#): trocar `isAiReply` por um helper único `isEligibleTarget(m, ctx)` (allowlist de §3.3). O rótulo do item do menu continua "Gerar melhoria".
2. `[paralelo]` [logic.py:218](#): o filtro de `targets` passa a aceitar `t.get("content") or t.get("media_type")`. A mensagem de erro só dispara com lista vazia de verdade.
3. `[paralelo]` [logic.py:244](#): o caminho *single* passa a gravar `media_type`/`media_path` na tabela filha (as colunas já existem desde a 002).
4. `[paralelo]` [extends.js](#) `ImproveDialog`: o payload single passa a mandar `media_type`/`media_path`/`role`/`status`/`sent_by_name`, como o multi já faz.
5. `[sequencial]` **Validação no servidor** (não confiar no cliente): `create_suggestion` recusa `role` fora da allowlist com 400. A rota é pública dentro da API.
6. `[paralelo]` O modal mostra a prévia certa para mídia: `🖼️ imagem` / `🎤 áudio` / `📄 documento` + a legenda, em vez de "(sem conteúdo)".

**Pronto quando:** botão direito numa mensagem do cliente, numa mensagem enviada pelo atendente e numa nota privada oferece "Gerar melhoria"; numa transcrição/card de sistema **não** oferece (nem menu abre); marcar uma imagem sem legenda cria a sugestão em vez de dar erro.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída
- **O que foi feito:** `logic.py`: `ELIGIBLE_TARGET_ROLES` + `_target_has_payload`/`_ineligible_role`; `create_suggestion` aceita alvo com mídia sem texto e recusa papel fora da allowlist (400). `extends.js`: `isEligibleTarget`/`mediaLabel`/`targetPayload`, os dois filtros (menu + lote) usam o helper, payload single/multi manda media/role/status/sent_by_name, prévia do modal mostra rótulo de mídia.
- **Como foi feito / decisões:** A tabela filha já gravava media_type/media_path (002) — a lacuna era o frontend não enviá-los e o filtro `.trim()` descartar mídia sem legenda. Validação de papel só quando o `role` está PRESENTE (compat com callers legados que não enviam role).
- **Problemas / pendências:** Nenhuma.
- **Verificação:** 3 testes novos (mídia sem legenda aceita, papel inelegível → 400, nota privada aceita) verdes. `node --input-type=module --check extends.js` OK.

---

### F3 — `risk.py`: o motor de classificação 🟢

**Objetivo:** um módulo puro que responde "que nível é esta mutação?".

**Itens** `[paralelo]`
1. Criar `src/risk.py`, sem importar DB nem rede. Superfície:
   ```python
   TIERS = ("basico", "avancado")
   STRUCTURAL_KEYS = {"tool_names", "routing_targets", "is_router",
                      "model_config", "hooks_config", "enabled"}
   def tier_for(tool_name: str, tool_input: dict | None, *,
                agent_exists: bool = True) -> str: ...
   def needs_db_write_key(tool_name: str) -> bool: ...
   def permission_key_for(tier: str) -> str: ...   # basico→"approve", avancado→"approve_advanced"
   def label_for(tier: str) -> str: ...            # "Básico" / "Avançado" (PT-BR, para a UI)
   ```
2. Implementar a tabela de §3.2. **Fallback avançado** para nome desconhecido, `tool_input` não-dict ou `None`.
3. `update_agent`: básico só quando `set(tool_input) - {"agent_key","change_note"} ⊆ {prompt, description, display_name}` **e** `agent_exists=True`.
4. Testes puros em `tests/python/test_risk.py`: uma asserção por linha da tabela + o caso de bypass (`update_agent{prompt}` = básico, `update_agent{prompt, tool_names}` = avançado) + nome desconhecido = avançado.

**Pronto quando:** `test_risk.py` cobre as 11 ferramentas e passa **sem banco** (`pytest tests/python/test_risk.py` isolado).

#### Status de execução — Fase 3
**Estado:** ✅ Concluída
- **O que foi feito:** Criado `src/risk.py` (puro) com `tier_for`/`needs_db_write_key`/`permission_key_for`/`label_for` + `STRUCTURAL_KEYS`. Criado `tests/python/test_risk.py` (16 casos).
- **Como foi feito / decisões:** Classificação por EFEITO (D1-a): `update_agent` lido pelas chaves de `tool_input` (texto/rótulo ⊆ {prompt,description,display_name} = básico; qualquer chave estrutural = avançado; agente inexistente = avançado). Fail-closed em nome desconhecido/`tool_input` não-dict. Tool names confirmados por ssh no executor (`waitForApproval(ctx,'<nome>',…)`).
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `pytest tests/python/test_risk.py` = 16 passed SEM banco (import por caminho de arquivo).

---

### F4 — RBAC: `chat` separada e a decisão por nível 🔴 [depende de: F3]

**Objetivo:** conversar deixa de exigir aprovar; aprovar passa a depender do nível.

**Itens**
1. `[sequencial]` [plugin.yaml](#) `rbac.permissions`: acrescentar
   | chave | rótulo |
   |---|---|
   | `chat` | Conversar com a IA de melhoria (sem aplicar mudanças) |
   | `approve_advanced` | Aprovar mudanças de risco **avançado** (criar agente/tool, código, estrutura) |
   | `approve_db_write` | Aprovar **escrita direta no banco** (sem versionamento e sem volta) |

   A `approve` **permanece** e passa a significar **nível básico** — retrocompatível: quem já tem continua aprovando o que aprovava de mais leve, e perde o resto (fail-closed é a direção segura).
2. `[sequencial]` [routes.py](#): trocar `plugin_permission("approve")` por `plugin_permission("chat")` nas rotas **113, 152, 189, 261, 277, 296**. `reject` (137) e `default-filter` (415) ficam em `approve`.
   ⚠️ Compatibilidade: um usuário que hoje tem `approve` e não ganhar `chat` **perderia o chat**. Implementar um helper local `any_permission("chat", "approve")` (dois `authz.acheck`, molde de [plugins/context.py:295](../plugins/context.py#L295)) e usá-lo nessas 6 rotas.
3. `[sequencial]` [routes.py:236](#) `POST /conversations/{cid}/approve`: sai o `plugin_permission("approve")` do decorator e entra checagem **dentro** do handler, porque o nível depende da linha:
   - lê o approval, recalcula `tier` (§3.2) e compara com o persistido → vale o mais alto;
   - `basico` ⇒ exige `plugin.melhorias.approve`;
   - `avancado` ⇒ exige `plugin.melhorias.approve_advanced`;
   - `db_write` ⇒ exige **as duas**: `approve_advanced` **e** `approve_db_write`;
   - faltando a chave: **403 com o motivo estruturado** `{error, tier, needed_permission, can_escalate}` — é o que o painel usa para oferecer "Encaminhar" (F7) em vez de só reclamar.
4. `[paralelo]` [chat_logic.py:374](#) `register_approval`: calcular e persistir `risk_tier`.
5. `[paralelo]` `GET /conversations/{cid}` passa a devolver `risk_tier` + `can_decide` por approval (o painel não deve adivinhar permissão).

**Pronto quando:** um usuário só com `chat` abre a melhoria, conversa e vê os cartões, mas o ✓ de um `create_tool` devolve 403 com `tier: "avancado"`; o mesmo usuário aprova um `patch_agent_prompt` normalmente.

#### Status de execução — Fase 4
**Estado:** ✅ Concluída
- **O que foi feito:** `risk.py` cabeado ao chat_logic (`compute_tier`/`effective_tier`/`_agent_exists`); `register_approval` persiste `risk_tier`; `decide_approval` grava `decided_by_name`. `routes.py`: helper `any_permission`, `_tier_block`, `_annotate_approvals`; 6 rotas approve→`any_permission(chat,approve)`; approve-por-mutação gateado por nível dentro do handler (403 estruturado); GET /conversations anota nível+can_decide. `plugin.yaml`: chaves `chat`/`approve_advanced`/`approve_db_write` (approve = nível básico).
- **Como foi feito / decisões:** Recusar (✕) exige só `chat` (sempre seguro); aprovar (✓) exige a chave do nível. `effective_tier` = max(persistido, recalculado). db_write exige approve_advanced E approve_db_write, e nunca é encaminhável (can_escalate=false).
- **Problemas / pendências:** `_tier_block`/`_annotate_approvals` chamam `agent_repo.get` no event-loop thread (lookup pequeno/raro) — aceitável; register roda em to_thread.
- **Verificação:** `test_risk_tier_gate` (usuário não-admin com chat+approve): básico 200, avançado 403 c/ tier, recusa 200, db_write exige chave própria. Suíte 77 passed.

---

### F5 — Contexto neutro + mídia 🟢 [depende de: F2]

**Objetivo:** a IA entende de quem é a mensagem marcada e enxerga a mídia.

**Itens**
1. `[sequencial]` [generation.py:280-300](#): rótulo por papel na montagem do histórico e do bloco de alvos.
   | Papel da linha | Rótulo |
   |---|---|
   | `user` | `Cliente` |
   | `assistant` sem `status='operator'` | `IA` |
   | `assistant` com `status='operator'` | `Atendente (<sent_by_name>)` ou `Automação` |
   | `private_note` | `Nota interna (<autor>)` |
2. `[sequencial]` O marcador de alvo passa a casar por **`_id`**, não por `role == "assistant" and content in unmarked` ([generation.py:286](#)) — casar por conteúdo já era frágil (duas mensagens idênticas) e quebra de vez com alvo do cliente.
3. `[sequencial]` Cabeçalho: `## Mensagem marcada pelo operador` + uma linha `Autor: <rótulo>`. O `DEFAULT_IMPROVEMENT_PROMPT` ([generation.py:53](#)) ganha uma frase: a mensagem marcada **pode não ser da IA** — pode ser do cliente, do atendente ou de uma automação externa, e nesse caso o diagnóstico é sobre **como a IA deveria ter reagido**, não sobre o texto marcado.
4. `[paralelo]` **Mídia → texto** (D6), em cascata: `media_caption` do core → `content` já composto (a descrição de imagem/extração de documento reescreve o `content`, ver CLAUDE.md) → transcrever/descrever sob demanda com `handler.transcribe_audio` / `handler.describe_image` ([agent/llm.py:159](../agent/llm.py#L159), [agent/llm.py:217](../agent/llm.py#L217)) → marcador `[imagem sem descrição]`.
5. `[paralelo]` **Imagem como anexo**: [chat_logic.py:441](#) `start_conversation` passa a montar `parts` quando há alvo `media_type='image'`, reusando `resolve_image_parts` ([chat_logic.py:554](#), já confina o path a `statics/`). Cap: **no máximo 3 imagens** e o teto de 5 MB que já existe.

**Pronto quando:** marcar uma mensagem do cliente produz um prompt inicial em que ela aparece como `Cliente:` e marcada; marcar uma imagem manda a imagem ao executor e a IA a descreve na primeira resposta.

#### Status de execução — Fase 5
**Estado:** ✅ Concluída
- **O que foi feito:** `generation.py`: `_role_label` (Cliente/IA/Atendente(<nome>)/Automação/Nota interna), `_derive_media_text` (transcrição/descrição sob demanda), `_MEDIA_KIND_LABEL`. Montagem do histórico + bloco de alvos reescritos: rótulo por papel, marcador neutro 'MENSAGEM MARCADA PELO OPERADOR', cabeçalho 'Mensagem marcada pelo operador' + 'Autor:'. `DEFAULT_IMPROVEMENT_PROMPT` ganhou a frase 'o alvo pode não ser da IA'. `chat_logic.start_conversation` anexa até 3 imagens marcadas (resolve_image_parts).
- **Como foi feito / decisões:** ⚠️ Desvio do plano: as linhas de contexto do LLM (`_row_to_dict`) NÃO carregam o id interno (só msg_id/ts). O marcador casa por TIMESTAMP (round 3 casas), não por _id — chave única da mensagem, funciona para qualquer papel; conteúdo é fallback para caller sem ts casável (testes). Autor do alvo resolvido do histórico por ts.
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `test_neutral_context_labels_client_target` (alvo do cliente → 'Autor: Cliente' + marcado). Teste legado de marcador atualizado ao texto novo. Suíte 78 passed.

---

### F6 — Executor: interruptor do `db_write` + consciência de risco 🟢 [repositório do executor]

**Objetivo:** desligar a ferramenta mais perigosa sem release do plugin **e** ensinar o executor a se comportar em volta do modelo de risco (D9) — sem lhe dar poder de classificar.

**Parte A — interruptor do `db_write`** `[paralelo]`
1. `src/env.ts`: `WBAI_DB_WRITE_ENABLED: z.coerce.boolean().default(true)` (default preserva o comportamento atual).
2. `src/core/tool-registry.ts:395`: a tool `db_write` só entra no array quando o interruptor está ligado **e** `WHATSBOT_RW_DSN` existe. Desligada, ela **não aparece** para o modelo — melhor que aparecer e recusar (o agente não gasta turno tentando).

**Parte B — consciência de risco (doc, não lógica — D9/§2.6)** `[paralelo]`
3. `src/core/system-prompt.ts`: acrescentar uma seção curta na parte "Escopo PERMITIDO / com aprovação" — **existem dois níveis** (básico/avançado), **prefira o básico** quando resolve (um atendente aprova sem escalar), e **avise o operador** quando o que você vai propor for avançado (criar/alterar tool, mexer em estrutura de agente, `db_write`) porque pode ir para uma fila em vez de sair na hora. ⚠️ Deixar explícito: **você não decide o nível — quem decide é o sistema**; sua parte é escolher a mudança mais simples que resolve e ser honesto sobre o efeito.
4. `src/guides/niveis-de-risco.md` *(novo)*: a tabela §3.2 em prosa, como referência sob demanda (`read_guide`). Cabeçalho avisando que é **descritivo** — a autoridade é o gateway.
5. Registrar o guia novo em `read_guide` (a lista de guias do executor — conferir `tool-registry.ts` / onde `read_guide` resolve os nomes).

⚠️ A **semântica da resposta de escalonamento** (`approved=false, reason="Encaminhado…"` = roteamento, não recusa) fica na **F7·7**, porque depende do texto exato do motivo, que a F7 define.

⚠️ Este é o **único** repositório fora do plugin neste plano. O repo do executor é local no servidor (sem remote) — commitar lá mesmo, como os commits existentes.

6. Deploy: `npm run deploy` no `/opt/whatsbot-ai-server` (build + restart + espera de health).

**Pronto quando:** com `WBAI_DB_WRITE_ENABLED=false` e restart, uma conversa nova não lista `db_write`; `read_guide("niveis-de-risco")` devolve a tabela; e numa conversa de teste, ao propor um `create_tool`, o executor avisa por conta própria que é mudança avançada. `GET /version` no hash novo.

#### Status de execução — Fase 6
**Estado:** ✅ Concluída
- **O que foi feito:** Executor (`/opt/whatsbot-ai-server`): `env.ts` ganhou `WBAI_DB_WRITE_ENABLED` (número 0/1, default 1); `tool-registry.ts` só registra `db_write` quando ligado E `WHATSBOT_RW_DSN` existe (spread condicional). `system-prompt.ts` ganhou a seção 'Níveis de risco e aprovação' + semântica do 'Encaminhado' (cobre também a F7·7). Novo guia `guides/niveis-de-risco.md` + registrado em `GUIDE_FILES`/`read_guide`.
- **Como foi feito / decisões:** ⚠️ Usei NÚMERO 0/1 (não boolean): `z.coerce.boolean('false')` coage para true (footgun) — mesmo padrão de `WBAI_INJECT_OAUTH_TOKEN`. Desligar = `WBAI_DB_WRITE_ENABLED=0`. A semântica do 'Encaminhado' entrou já na F6 (o texto do motivo é reconhecido por intenção 'Encaminhar/fila/nível maior'), então F7·7 não exige 2º deploy.
- **Problemas / pendências:** Verificação de que db_write SOME com =0 depende de uma conversa viva (não testado end-to-end); caminho de código é direto.
- **Verificação:** `npm run build` (tsc) limpo; `npm run deploy` health OK, rodando hash 6854ea0. Commit local no repo do executor (sem remote).

---

### F7 — Fila de escalonamento e reaplicação 🔴 [depende de: F1, F3, F4] — **fase mais arriscada**

**Objetivo:** o que passa do nível não se perde nem prende o executor.

**Itens**
1. `[sequencial]` Fatorar as mutações de [internal_routes.py:247-410](#) para um módulo chamável (`mutations.py`) com uma função por ferramenta e uma assinatura comum `apply(tool_name, tool_input, *, user) -> (ok, data, error)`. O `internal_routes` passa a ser casca fina sobre ele — **mesmo padrão `routes.py` → `logic.py` que o plugin já usa**. Sem duplicar o versionamento.
2. `[sequencial]` `POST /conversations/{cid}/approve` aceita `{escalate: true}`: grava `_escalations` (status `pendente`), carimba `approvals.escalated_at`, responde ao executor com `approved=false` + motivo padronizado, e **não** decide o approval (`approved` continua `NULL`? **não** — grava `approved=0` com `reason` da escalada, senão a idempotência de [chat_logic.py:411](#) permite decidir de novo).
   ⚠️ Detalhe: `decide_approval` recusa linha já decidida. A escalada **é** uma decisão (recusa com motivo); o que fica pendente é a linha em `_escalations`, não o approval.
3. `[sequencial]` `replay.py`: `apply(escalation_id, *, user)`:
   - relê o estado atual do alvo e **recusa** se mudou desde `created_at` (P1);
   - chama `mutations.apply(...)` **on-behalf-of quem decidiu**;
   - grava `status`, `applied_at`, `error`;
   - chama `audit(...)` (F11);
   - `broadcast("plugin_melhorias_escalation_changed", …)` para o painel atualizar ao vivo.
4. `[sequencial]` Rotas da fila: `GET /escalations?status=` (`chat`), `POST /escalations/{id}/decide` (checagem de nível **igual** à de F4, dentro do handler).
5. `[sequencial]` **`db_write` nunca enfileira** (D4): `escalate: true` sobre `db_write` devolve 400 com a mensagem de §5.
6. `[paralelo]` Testes: escalada → fila → sênior aprova → `agent_repo` versionado; sênior sem chave → 403; alvo alterado no meio → `falhou` com motivo; `db_write` → 400.
7. `[sequencial]` **Executor — semântica do "Encaminhado" (D9/§2.6, complementa F6·B)** `[repositório do executor]`: com o texto do motivo já fixado no item 2, acrescentar ao `src/core/system-prompt.ts` que `approved=false` com esse motivo é **roteamento para aprovação de nível maior, não recusa da ideia** — não reformular nem re-propor a mesma mutação na conversa; explicar ao operador que ficou na fila e seguir. Deploy junto (ou logo após) o da F6. ⚠️ O motivo devolvido pelo gateway (F7·2) precisa ser **estável** — o executor casa pela intenção, mas mudanças bruscas de texto confundem; manter a frase curta e constante.

**Pronto quando:** o roteiro de §5 roda inteiro contra o Postgres de teste, com o executor **simulado** (fake), e a versão do agente sobe de N para N+1 com `change_note` citando a melhoria e o aprovador; e numa conversa real, ao receber o "Encaminhado", o executor avisa o operador e **não** re-propõe.

#### Status de execução — Fase 7
**Estado:** ✅ Concluída
- **O que foi feito:** Fatorado `mutations.py` (escrita versionada + resource_of/current_version/required_permissions/audit_applied); `internal_routes` virou casca fina sobre ele. `chat_logic.escalate_approval`/`get_escalation`/`list_escalations`/`mark_escalation` + `effective_tier`. `replay.py` (RBAC do sênior + P1 + mutations.apply). Rotas: `/conversations/{cid}/approve` aceita `{escalate}`; `GET /escalations`, `POST /escalations/{id}/decide`. Migração 005 ganhou resource_kind/resource_id/baseline_version.
- **Como foi feito / decisões:** db_write NUNCA enfileira (400). P1 por VERSÃO do alvo (baseline capturado no encaminhar × versão atual no replay); descoberto que `agent_repo.save` só bumpa versão em mudança real (o teste P1 edita o prompt de verdade). Auditoria da mutação vive em `mutations` (ponto onde caminho direto e fila convergem) — replay não audita de novo.
- **Problemas / pendências:** P2 (on_behalf_of do caminho direto) fica como está (recomendação a). O approval decidido no encaminhar grava approved=0 + escalated_at (idempotência).
- **Verificação:** `test_escalation_flow` (encaminhar→fila→sênior aplica→versão sobe; atendente 403; db_write 400) e `test_replay_refuses_changed_target` (P1) verdes. Suíte 80→87.

---

### F8 — Autoria no chat 🟢 [depende de: F1]

**Objetivo:** saber quem falou o quê, no chat e no cartão de aprovação.

**Itens** `[paralelo]`
1. [chat_logic.py:335](#) `append_chat_message` ganha `author_user_id`/`author_name` e [routes.py:189](#) os preenche do `current_user` (o `_actor` já existe, [routes.py:33](#)).
2. [chat_logic.py:411](#) `decide_approval` grava `decided_by_name`.
3. `list_chat_messages` e `list_approvals` devolvem os campos novos.
4. [chat.js:61](#) `UserCard`: cabeçalho com o nome (`text-[11px] text-wa-secondary`, molde do `sent_by_name` das bolhas do core). Sem nome (linhas legadas/instalação aberta) ⇒ o balão fica como hoje.
5. [chat.js:87](#) `ApprovalCard`: o rodapé decidido mostra "✔ aprovado por Fulano · <data>" + o **selo de nível** (F12).
6. Mensagens do executor (`assistant`) não ganham autor — `author_name` fica vazio e o balão é o de sempre.

**Pronto quando:** dois usuários diferentes conversando na mesma melhoria aparecem nomeados; recarregar a página mantém os nomes (vêm do banco, não do estado local).

#### Status de execução — Fase 8
**Estado:** ✅ Concluída
- **O que foi feito:** `append_chat_message` ganhou `author_user_id`/`author_name`/`audio_path`; a rota de mensagem preenche do `current_user`. `decide_approval` grava `decided_by_name` (já na F4). `list_chat_messages`/`list_approvals` devolvem os campos (via dict(row)). Frontend: `UserCard` mostra o nome do autor; `ApprovalCard` mostra 'aprovado por X'.
- **Como foi feito / decisões:** Mensagens do executor (assistant) não ganham autor (author_name vazio → balão como antes).
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `test_chat_message_author` (dois campos vêm do banco na hidratação) verde.

---

### F9 — Áudio no chat 🟢 [depende de: F1]

**Objetivo:** ditar em vez de digitar, com o áudio preservado.

**Itens**
1. `[sequencial]` Nova rota `POST /conversations/{cid}/audio` (multipart, `any_permission("chat","approve")`):
   - grava em `statics/melhorias_audio/<cid>/<uuid>.ogg` (teto **5 MB**, extensão/MIME validados, path montado no servidor — **nunca** com nome vindo do cliente);
   - transcreve com `handler.transcribe_audio(path, phone="")` ([agent/llm.py:159](../agent/llm.py#L159)) em `asyncio.to_thread`;
   - `append_chat_message(role="user", content=<transcrição>, audio_path=…, author_*)`;
   - manda a transcrição ao executor **com atribuição**: `"[ditado por <nome>] <transcrição>"`;
   - transcrição vazia ⇒ 422 "não consegui entender o áudio", **sem** mandar nada ao executor e sem gravar linha (evita balão fantasma).
2. `[paralelo]` [chat.js:431](#): botão de microfone ao lado do de imagem, reusando `window.Recorder` (opus-recorder já carregado globalmente em [web/index.html:51](../web/index.html#L51)) ou importando `/static/js/components/contacts/hooks/useAudioRecorder.js`. Estados: gravando (contador) → enviando → balão com player.
3. `[paralelo]` O balão do ditado mostra `<audio controls>` apontando para `/statics/melhorias_audio/...` + a transcrição como texto (D8).
4. `[paralelo]` Guardas do navegador que o hook do core já traz: sem `Recorder` carregado e fora de HTTPS/localhost, o botão nem aparece.

⚠️ `statics/` é **disco local da instância** (CLAUDE.md §gotchas): no Coolify exige Persistent Storage em `/app/statics`, que **já está mapeado**. Instância sem o mount perde o áudio no redeploy — a transcrição, que está no Postgres, sobrevive.

**Pronto quando:** gravar 10s no painel produz um balão com player + texto, a IA responde ao conteúdo ditado, e o F5 do navegador mantém os dois.

#### Status de execução — Fase 9
**Estado:** ✅ Concluída
- **O que foi feito:** Rota `POST /conversations/{cid}/audio` (multipart): grava em `statics/melhorias_audio/<cid>/<uuid>.<ext>` (path do SERVIDOR, uuid+cid saneado, teto 5 MB, MIME validado), transcreve com `handler.transcribe_audio`, persiste a linha com `audio_path` + autor, manda ao executor `[ditado por <nome>] <texto>`. Transcrição vazia ⇒ 422 + apaga o arquivo. Frontend: microfone (reusa `useAudioRecorder` do core, OGG/Opus) + `<audio controls>` no balão.
- **Como foi feito / decisões:** Botão de mic só aparece com `window.Recorder` carregado (guarda do hook). Auto-resume no 404 do executor, como o envio de texto.
- **Problemas / pendências:** `statics/` é disco local (Persistent Storage no Coolify já mapeado); a transcrição sobrevive no Postgres se o arquivo sumir.
- **Verificação:** `test_audio_dictation` (transcreve+grava+manda), `test_audio_empty_transcription_is_422`, `test_audio_bad_format_is_415` verdes.

---

### F10 — Melhoria avulsa 🟢 [depende de: F1, F5]

**Objetivo:** criar melhoria sem sair do painel e sem mensagem vinculada.

**Itens**
1. `[sequencial]` [logic.py:206](#) `create_suggestion` ganha o caminho `kind="standalone"`: sem `phone`, sem `contact_repo.get_by_phone`, sem `_post_system_notice` ([logic.py:299](#) — não há conversa onde postar).
2. `[sequencial]` **Parser de permalink** (puro, testável): `parse_message_link(url) -> {conversation_id, message_db_id} | None` para `<base>/conversations/<id>?message=<mid>`. Link válido ⇒ resolve a conversa, valida que existe, e a sugestão vira `kind="message"` normal, com todo o contexto. Link inválido/vazio ⇒ segue avulsa (**nunca** erro bloqueante).
3. `[sequencial]` [generation.py:160](#) `build_analysis_payload` ganha ramo sem contato: pula histórico e execução, mantém o **inventário de agentes/tools/variáveis** (que é o que a IA precisa para propor mudança) e usa o texto do pedido como seção principal.
4. `[paralelo]` [panel.js](#): botão **"Nova melhoria"** ao lado de "Recarregar" ([panel.js:427](#) vizinhança), gateado por `can('request')`. Modal: textarea obrigatório + campo "Link da mensagem (opcional)" + dica de onde copiar o link.
5. `[paralelo]` `_suggestion_dict` ([logic.py:194](#)): avulsa não tem `conversation_url`; o painel mostra "—" (o caminho de conversa apagada já existe e cobre a renderização).
6. `[paralelo]` Colunas do painel ([panel.js:91](#) `buildCols`): `Contato` mostra "— (avulsa)".

**Pronto quando:** "Nova melhoria" sem link cria linha `pendente` sem contato, abre chat e a IA responde com base no inventário; com link colado, a melhoria fica idêntica à criada pelo botão-direito.

#### Status de execução — Fase 10
**Estado:** ✅ Concluída
- **O que foi feito:** `logic.parse_message_link` (puro), `_anchor_from_link` (resolve link→conversa/mensagem), `_create_standalone`. `create_suggestion` ganhou `kind='standalone'` + `message_link`: link válido vira message-anchored, senão avulsa (contact_id NULL). `generation._standalone_payload` (inventário + pedido, sem histórico). Frontend: botão '+ Nova melhoria' + modal (texto + link) e coluna Contato mostra '— (avulsa)'.
- **Como foi feito / decisões:** A rota `/suggestions` recebe `kind`/`message_link`. build_analysis_payload cai em `_standalone_payload` quando phone vazio (start_conversation da avulsa funciona sem contato).
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `test_parse_message_link`, `test_standalone_suggestion_without_link`, `test_standalone_with_valid_link_becomes_message` verdes.

---

### F11 — Auditoria 🟢 [depende de: F7]

**Objetivo:** toda mudança na IA feita por aqui aparece em `/audit`.

**Itens** `[paralelo]`
1. Import defensivo de `plugins.context.audit` ([plugins/context.py:235](../plugins/context.py#L235)).
2. Ações (namespaceadas com o id do plugin, regex `PLUGIN_ACTION_RE`):
   | Ação | `resource_type` / `resource_id` | Quando |
   |---|---|---|
   | `melhorias.mutacao.aplicada` | `agent`/`tool`/`variable` + a chave | ✓ que resultou em escrita |
   | `melhorias.mutacao.recusada` | idem | ✕ com motivo |
   | `melhorias.mutacao.escalonada` | idem | encaminhada à fila |
   | `melhorias.db_write.aplicado` | `data` / `plugin:melhorias` | `db_write` aprovado (o SQL vai no `after`) |
   | `melhorias.config.alterada` | `plugin:melhorias` | `PUT /config` ([routes.py:434](#)) |
3. ⚠️ **Ponteiro, nunca cópia** (CLAUDE.md §Auditoria): conteúdo versionado entra como `{key, version, tier}` — o prompt inteiro **não** é copiado para a trilha (ele já está em `ai_agents_history`).
4. ⚠️ **Nada de segredo**: o `ai_server_secret` nunca entra em `before`/`after` (o mascaramento por nome do `audit_repo` é rede, não licença).
5. ⚠️ **Mensagem de chat NÃO entra na trilha** — é conversa, e o histórico dela já é `plugin_melhorias_ai_messages` com autor (F8). Mesma regra do core para `message.*`.
6. Ator: quem decidiu. Reaplicação da fila carimba o **sênior**, não quem abriu a conversa.

**Pronto quando:** aprovar um `patch_agent_prompt` grava linha em `/audit` com ação `melhorias.mutacao.aplicada`, recurso `agent:<key>`, ator correto e IP; e o filtro por recurso `agent` mostra a mudança ao lado das feitas pela tela de IA.

#### Status de execução — Fase 11
**Estado:** ✅ Concluída
- **O que foi feito:** `mutations.audit_applied` (ponteiro key/version/tier; ator do ContextVar) chamado nas 8 escritas → cobre caminho direto (via internal_routes) E fila (via replay). Rotas auditam `mutacao.escalonada`, `mutacao.recusada`, `db_write.aplicado` (SQL literal no after — única trilha dessa escrita) e `config.alterada` (só as chaves; secret nunca em claro).
- **Como foi feito / decisões:** CONVERSA não entra na trilha (só decisões/mutações). O ator viaja pelo ContextVar da request (executor on-behalf-of no direto; sênior na fila). `audit_enabled` default ON.
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `test_mutation_applied_is_audited` (audit_repo.count sobe em 1 com ação/ recurso corretos) verde.

---

### F12 — Painel: selo de risco, fila e botão 🟢 [depende de: F7, F10]

**Objetivo:** o operador vê o nível antes de clicar e o sênior acha o que está esperando por ele.

**Itens** `[paralelo]`
1. `ApprovalCard` ([chat.js:87](#)): selo do nível — `Básico` (`bg-wa-teal/10 text-wa-teal`) / `Avançado` (âmbar) / `Escrita no banco` (vermelho). Sem chave, os botões ✓/✕ ficam desabilitados e aparece **"Encaminhar para aprovação"**, com o texto do 403 estruturado de F4.
2. Aba **"Fila de aprovação"** no painel: pendentes com ferramenta, resumo, nível, quem pediu, quando, e ✓/✕ para quem tem a chave. Contador no cabeçalho.
3. Botão **"Nova melhoria"** (F10) e coluna `Tipo` (mensagem/avulsa) na lista.
4. ⚠️ Tempo real: assinar `plugin_melhorias_escalation_changed` via `api.services.subscribe`/`wsBus` — **jamais** `new WebSocket('/ws')` (CLAUDE.md; o socket cru é fechado com 4401). Refetch com **debounce + jitter**.
5. ⚠️ Modo escuro: só classes `wa-*`/`.wa-field`; o selo âmbar/vermelho usa as tintas já cobertas pelos overrides `html.dark` do `custom.css`. Testar com o tema escuro ligado.

**Pronto quando:** atendente vê o selo e o botão de encaminhar; sênior vê o contador da fila subir sem F5 e resolve por lá; ambas as telas legíveis no modo escuro.

#### Status de execução — Fase 12
**Estado:** ✅ Concluída
- **O que foi feito:** `ApprovalCard`: selo de nível (Básico/Avançado/Escrita no banco) + botão 'Encaminhar' quando o usuário não decide (anotado pelo backend ou revelado por um 403 estruturado); rodapé 'aprovado por X'/'Encaminhada'. `chat.js`: ações `escalate()` + tratamento do 403 no `decide()`. `panel.js`: botão '+ Nova melhoria', botão 'Fila de aprovação' com badge de contagem (WS `plugin_melhorias_escalation_changed`) + `QueueModal` (✓ aplica via replay / ✕ recusa), `NewImprovementModal`.
- **Como foi feito / decisões:** Tempo real via `subscribe`/`wsBus` (nunca `new WebSocket('/ws')`). Selo usa tintas cobertas pelos overrides html.dark. Cards ao vivo caem no 403-fallback (não há contexto de usuário no evento do executor).
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `node --input-type=module --check` OK em chat.js/chat_core.js/panel.js; JS tests 35 verdes; suíte Python 88 verde.

---

### F13 — Testes, build, publicação e instalação 🔴

**Objetivo:** o que foi feito chega a quem usa, sem regressão e sem versão fantasma.

**Itens** `[sequencial]`
1. Bump `plugin.yaml` para **1.8.0** + descrição das 5 frentes (padrão do arquivo: um parágrafo por versão).
2. `cd ../whatsbot-pro-plugins && python3 scripts/test_plugins.py melhorias` verde.
3. `node --test` nos módulos puros (incluindo o novo parser de permalink, se ficar em JS).
4. `venv/bin/python -m pytest tests/integration tests/contracts` no core — **nada** deve mudar (o plano não toca o core).
5. `python3 scripts/build_plugins.py melhorias` e depois `--check` limpo. ⚠️ Se o `--check` acusar "outdated" sem diferença de conteúdo, conferir **permissão do zip** antes de rebuildar (memória `build-plugins-check-falso-outdated-umask`).
6. ⚠️ **Antes de publicar**: `git fetch` no repo de plugins **e** consultar a tabela `plugins` de PRODUÇÃO — outra pessoa pode ter publicado uma versão no meio do trabalho (memórias `paridade-plugin-exige-checar-remoto` e `versao-de-plugin-pode-ser-publicada-no-meio-do-trabalho`).
7. **Instalar o zip no ambiente local** antes de commitar (memória `plugin-instalar-local-antes-de-commitar`): `storages/plugins/melhorias/` precisa refletir a 1.8.0, senão a suíte de integração do core testa outra coisa.
8. Deploy do executor (F6) **antes** do zip: com `db_write` ainda ligado nada quebra, mas a ordem correta evita um cartão de `db_write` sem interruptor.
9. **Grants em produção** (manual, documentar no Status): decidir quem recebe `chat`, `approve`, `approve_advanced`, `approve_db_write`. Sugestão inicial — cargo **Atendente**: `chat` + `approve`; cargo **Gestor**: + `approve_advanced`; `approve_db_write`: ninguém (Administrador já faz bypass).
10. ⚠️ **Revisar o cargo Atendente**: ele tem hoje `agent.prompts.edit`, `agent.prompts.version` e `agent.variables.manage` (§2.5) — provavelmente herança acidental. Retirá-las é o que faz a segunda camada voltar a valer. **Decisão do usuário**, ver P3.

**Pronto quando:** zip publicado, instalado localmente, produção atualizada, e um teste de ponta a ponta com dois logins diferentes (atendente e sênior) percorre escalada → aprovação → linha em `/audit`.

#### Status de execução — Fase 13
**Estado:** 🟡 Em andamento
- **O que foi feito:** plugin.yaml → 1.8.0 + parágrafo de descrição das 5 frentes; melhorias.json e catalog.json sincronizados; `build_plugins.py melhorias` gerou o zip (25 arquivos) e `--check` limpo; instalado em storages/plugins/melhorias (1.8.0).
- **Como foi feito / decisões:** Executor (F6) já deployado antes do zip. NÃO publiquei no repo externo (git push) nem apliquei grants de produção — são ações outward-facing que dependem de confirmação do usuário (ver P3 e F13·6/9/10).
- **Problemas / pendências:** PENDENTE (ação do usuário): (1) git push do repo whatsbot-pro-plugins publicando o zip 1.8.0; (2) grants de produção — sugestão: Atendente=chat+approve, Gestor=+approve_advanced, approve_db_write=ninguém; (3) revisar o cargo Atendente (P3: retirar agent.prompts.edit/version + agent.variables.manage); (4) conferir a tabela plugins de PROD antes de publicar.
- **Verificação:** `test_plugin_api_surface` (core) verde — o core não mudou. Suíte do plugin 88 py + 35 js verde no banco isolado whatsbot_test_plano128.

---

## 7. Riscos e cuidados

| # | Ponto | Risco | Mitigação |
|---|---|---|---|
| **R1** | Bypass de nível trocando de ferramenta | `update_agent{prompt}` = `patch_agent_prompt`; classificar por nome deixaria a porta aberta | D1-a: classificação por **efeito**, lendo `tool_input` (§3.2), com teste dedicado do par |
| **R2** | Timeout de 5 min do executor | Enfileirar segurando a promise falha em silêncio (5 min ou restart) e prende um runner | D3-a: responder na hora e **reaplicar** depois. Nunca aumentar `APPROVAL_TIMEOUT_MS` como "solução" |
| **R3** | Reaplicação sobre estado mudado | O sênior aprova horas depois um patch escrito contra a versão N; o agente já está em N+2 | `replay.apply` relê e **recusa** se o alvo mudou (P1); mensagem manda reabrir a análise |
| **R4** | Perda silenciosa de acesso na atualização | Quem tinha `approve` perde as rotas de chat quando elas viram `chat` | `any_permission("chat","approve")` nas 6 rotas (F4·2) |
| **R5** | Áudio ocupando disco | `statics/` é disco local, sem poda | Teto de 5 MB por gravação; poda futura por idade (**P4**, adiado). ~120 MB/ano é aceitável |
| **R6** | Falsa sensação de contenção | Os níveis governam as **mutações**; o agente tem Bash com `bypassPermissions` no host dele (§2.4) | Escrito no plano e no rótulo da chave `approve_advanced`. Fronteira real continua sendo a ausência de GRANT de escrita no DSN do shell |
| **R7** | `db_write` fora de tudo | Sem versionamento, sem RBAC do core, sem rollback | Duas chaves (D4) + interruptor no executor (F6) + auditoria com o SQL literal (F11) |
| **R8** | Mídia inflando o payload da IA | Imagem base64 no `start` pode estourar contexto | Máx. 3 imagens, 5 MB cada, o mesmo cap de [chat_logic.py:554](#) |
| **R9** | Ordem da migração em produção | Produção pode estar com plugin/migração atrás | Conferir `plugin_migrations` **antes** de instalar; a 005 é aditiva e o `DROP NOT NULL` é reversível |
| **R10** | Modo escuro nas telas novas | Selo/fila/balão de áudio com cor crua ficam ilegíveis | Só `wa-*`/`.wa-field`; checklist final com o tema escuro ligado |
| **R11** | Duplicação de lógica de mutação | `mutations.py` + `internal_routes.py` podem divergir | F7·1 fatora — o `internal_routes` vira casca fina, sem segunda implementação |
| **R12** | Chat de melhoria virando ruído na trilha | Auditar cada mensagem inundaria `/audit` | F11·5: só decisões e mutações entram; as mensagens ficam na tabela do plugin, com autor |

---

## 8. Perguntas em aberto

**P1 — Reaplicação sobre alvo alterado.** ⏸️ AGUARDANDO (recomendação abaixo)
Quando o sênior aprova a fila e o agente/tool já mudou desde o pedido, o que fazer?
(a) **Recusar** e pedir para reabrir a análise — o sênior aprovou um texto que não é mais o que vai entrar; (b) aplicar mesmo assim (o versionamento permite reverter).
**Recomendação: (a).** O propósito do gate é o humano aprovar o que ele leu. Aplicar outra coisa quebra isso — e o custo de (a) é reabrir uma conversa.

**P2 — `on_behalf_of` no caminho direto.** ⏸️ AGUARDANDO
Hoje toda mutação é gravada em nome de quem **abriu** a conversa, não de quem aprovou (§2.4). A fila (F7) conserta o caminho dela. Conserto do caminho direto exige o executor mandar o aprovador no `X-WB-On-Behalf-Of` — hoje ele manda o `ctx.userId` fixo do runner.
(a) Deixar como está e documentar (a auditoria de F11 já registra o aprovador correto, mesmo que o repo versione o outro nome); (b) mudar o executor para propagar quem decidiu.
**Recomendação: (a) agora, (b) num plano seguinte.** (b) mexe no `conversation-runner` e no contrato de decisão — escopo próprio.

**P3 — Limpar o cargo Atendente.** ⏸️ AGUARDANDO (decisão do usuário)
O cargo tem hoje `agent.prompts.edit`, `agent.prompts.version` e `agent.variables.manage` (§2.5), o que anula a segunda camada de defesa.
(a) Retirar as três (recomendado — o atendente passa a mexer na IA **só** pelo fluxo de melhoria, com nível); (b) manter.
**Recomendação: (a)**, executado na F13·10, depois de confirmar que ninguém usa a tela de IA diretamente.

**P4 — Poda dos áudios.** ⏸️ ADIADO
~120 MB/ano não justifica poda agora. Reavaliar quando `statics/melhorias_audio/` passar de 1 GB.

**P5 — Notificar o sênior da fila.** ⏸️ ADIADO
D3 pediu fila, não notificação. Se a fila ficar parada, o caminho pronto é o motor de alerta por Telegram do `whatsapp_cloud`/`gowa` (agregação + cooldown já resolvidos lá).

**P6 — Fonte única da tabela de risco (plugin × doc do executor).** ⏸️ AGUARDANDO (recomendação abaixo)
O `risk.py` (autoritativo) e o `niveis-de-risco.md` (descritivo, no executor) descrevem a MESMA tabela §3.2 em dois repositórios. Podem derivar com o tempo.
(a) **Aceitar o drift** — a doc é só UX e o pior caso é o executor prometer "sai rápido" numa coisa que cai na fila (§2.6); manter as duas com um comentário cruzado ("ao mudar `risk.py`, revise o guia"); (b) **gerar o guia a partir do `risk.py`** — um script que serializa a tabela para markdown, publicado no `guides/` do executor no deploy.
**Recomendação: (a) agora.** A tabela muda raramente (só quando o executor ganha ferramenta nova, o que já é um deploy do executor) e (b) acopla os dois repositórios por um pipeline que hoje não existe. Reavaliar se a tabela começar a mudar com frequência. É este o único ponto para o qual o **acesso ao banco** que você ofereceu seria útil — e mesmo assim não é: a tabela é código, não dado.

---

## 9. Apêndice — arquivos-chave

**Plugin `melhorias`** (`../whatsbot-pro-plugins/plugins/melhorias/src/`) — **fonte de dev**; espelhar em `storages/plugins/melhorias/` ao instalar:

| Camada | Arquivo | Fases |
|---|---|---|
| Manifesto | `plugin.yaml` | F4, F13 |
| DB | `migrations/005_risco_autoria_audio.sql` *(novo)* | F1 |
| Lógica | `risk.py` *(novo)* · `mutations.py` *(novo)* · `replay.py` *(novo)* | F3, F7 |
| Lógica | `logic.py` (206, 218-224, 244, 299) | F2, F10 |
| Lógica | `chat_logic.py` (335, 374, 411, 441, 554) | F4, F5, F7, F8 |
| Lógica | `generation.py` (53, 160, 280-300, 407) | F5, F10 |
| Rotas | `routes.py` (113-307, 434) · `internal_routes.py` (247-410) | F4, F7, F9, F11 |
| Front | `static/extends.js` (42, 59) | F2 |
| Front | `static/chat.js` (61, 87, 289, 431) | F8, F9, F12 |
| Front | `static/panel.js` (91, 149, 427, 567) | F10, F12 |
| Testes | `tests/python/test_risk.py` *(novo)* · `test_suggestions.py` · `test_agent_gateway.py` | F0, F3, F7 |

**Executor** (`<host-do-executor>:/opt/whatsbot-ai-server/src/`): `env.ts` (23), `core/tool-registry.ts` (395), `core/system-prompt.ts`, `guides/niveis-de-risco.md` *(novo)* — F6, F7·7. Doc de trabalho: `/home/whatsbot-ai/work/CLAUDE.md` + `docs/` (ponteiro opcional).

**Core (somente leitura — nada é alterado):** [ContactDetail.js:829](../web/static/js/components/contacts/ContactDetail.js#L829) · [SystemMessageCard.js:59](../web/static/js/components/contacts/SystemMessageCard.js#L59) · [useMessageActions.js:113](../web/static/js/components/contacts/hooks/useMessageActions.js#L113) · [useAudioRecorder.js](../web/static/js/components/contacts/hooks/useAudioRecorder.js) · [agent/llm.py:159](../agent/llm.py#L159) · [plugins/context.py:235](../plugins/context.py#L235) · [db/audit_actions.py](../db/audit_actions.py)

---

## 10. Checklist de verificação

- [ ] `python3 scripts/test_plugins.py melhorias` verde (repo de plugins)
- [ ] `node --test` verde nos módulos puros do plugin
- [ ] `venv/bin/python -m pytest tests/integration tests/contracts` verde no core (**nada mudou lá**)
- [ ] Suíte rodada contra o **Postgres de teste** (`WHATSBOT_TEST_DB_URL`), sem outro pytest concorrente no mesmo banco
- [ ] Migração 005 aplica e a instalação sobe do zero
- [ ] Menu de contexto: aparece em cliente / atendente / nota privada; **não** aparece em card de sistema
- [ ] Imagem sem legenda e áudio viram sugestão (não dão "Mensagem inválida")
- [ ] Usuário só com `chat` conversa mas leva 403 num ✓ avançado, com `tier` no corpo
- [ ] Escalada → fila → aprovação do sênior → versão nova no `ai_agents_history`
- [ ] `db_write` acima do nível é **recusado**, não enfileirado
- [ ] Cada mutação aplicada gera linha em `/audit` com o ator certo; **nenhuma** mensagem de chat entra na trilha
- [ ] Nenhum segredo em URL, log ou `before`/`after` de auditoria
- [ ] Balão de autoria, selo de risco, fila e player de áudio legíveis no **modo escuro**
- [ ] Tela do plugin não abre `new WebSocket('/ws')` (só `subscribe`/`wsBus`)
- [ ] Restart do plugin (toggle) não deixa consumidor SSE nem fila órfã
- [ ] `build_plugins.py --check` limpo e zip **instalado** em `storages/plugins/melhorias/`
- [ ] Executor: `npm run deploy` com health verde e `/version` no hash novo
- [ ] Executor **descreve** o risco mas **não** o decide: `read_guide("niveis-de-risco")` responde; ao propor um `create_tool` o executor avisa que é avançado; ao receber "Encaminhado" não re-propõe. A classificação continua 100% no `risk.py` (teste: forjar `tool_input` avançado num nome básico ⇒ gateway trata como avançado)
- [ ] Grants de produção revisados e anotados (F13·9 e P3)
