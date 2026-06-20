# Registro de Decisões — Perguntas em aberto

> Rastreamento das respostas do Thiago às 83 perguntas consolidadas em
> [`00-plano-mestre.md` §5](00-plano-mestre.md). Fonte da verdade das decisões.
> Numeração global **P1..P83**. Última atualização: **2026-06-18**.

## Decisão global — Banco de dados (2026-06-18)

**A versão Pro pode assumir PostgreSQL.** Sempre que o SQLite **não** der para fazer algo de forma
limpa, vamos **direto para o Postgres** em vez de contorcer a solução. Mantemos compatibilidade com
SQLite onde for barato (não quebrar a versão EXE/single), mas o Postgres é o backend de referência
do Pro e features que dependem dele podem **exigi-lo**.

Impacto nas perguntas já decididas:
- **P6 (display_id):** a tabela-contador continua valendo (funciona nos dois, sem custo). Se um dia
  só Postgres, dá para trocar por SEQUENCE — mas não precisa.
- **P55 (filtro de custom attrs):** no Postgres liberamos **JSONB + índice GIN** (bem melhor); no
  SQLite fica o índice de expressão. Sem trava.
- **P74 (imutabilidade da auditoria):** append-only forte via **trigger/role do Postgres** passa a
  ser caminho aceitável de exigir (auditoria é feature Pro).
- **P42 (índice único de quick replies):** índices parciais funcionam nos dois; sem mudança.

> Regra prática: **projetar para os dois, mas não sacrificar uma boa solução Postgres por causa do
> SQLite.** Quando um recurso for Postgres-only, documentar e degradar com elegância no SQLite (ou
> exigir Postgres para aquele recurso).

## Decisões de Frontend/UX (2026-06-19)

- **Troca de caixa de entrada = RAIL DE ÍCONES** (estilo Chatwoot): coluna fina na extrema esquerda,
  um ícone por inbox/canal + "Todas". Atendente vê só as inboxes em que é membro (P9); admin vê todas.
- **Plano de frontend consolidado:** documento `10-plano-frontend-ux.md` amarra layout,
  navegação, componentes, fluxos e decisões de UX. O WhatsBot **evolui** os 3 painéis que já existem
  (`ContactList` | `ContactDetail` | `ContactInfoPanel`), não reescreve. Não precisa ser idêntico ao
  Chatwoot — foco em ser funcional e ter boa experiência.

### Perguntas de frontend (FQ1–FQ7) — decididas 2026-06-19

| FQ | Decisão |
|----|---------|
| **FQ1** | Rail de inboxes aparece **só com ≥2 inboxes** (opção b): instalação migrada de 1 número fica idêntica ao hoje; o rail surge quando o admin adiciona o 2º canal. |
| **FQ2** | Toggle de IA por nível no lugar certo da cascata: **global → Configurações**, **inbox → tela de Canais**, **conversa → header/painel da conversa** (opção a). |
| **FQ3** | **4 abas** no MVP (Abertas/Minhas/Não atribuídas/Resolvidas), opção a; evoluir para abas configuráveis quando houver views salvas. |
| **FQ4** | **Ordenar por última atividade da conversa, mais recente no topo** (a conversa sobe ao chegar/sair mensagem). Vale para todas as abas. *Encerra o P81 — comentário "não subir" retirado.* ⚠️ Se for desejada fila estrita por ordem de chegada só em "Não atribuídas", é ajuste futuro. |
| **FQ5** | Atributos de contato e de conversa no mesmo painel em **dois grupos rotulados** ("Dados do contato" / "Dados desta conversa"), opção a, respeitando faseamento FF3→FF5. |
| **FQ6** | Telas de gestão (Usuários/Canais/Atributos) em **full-page** como Plugins/Tools/Custos (opção a); "Configurar" de plugin segue em modal. |
| **FQ7** | Indicador de canal: **nome do canal no header** + **ícone/cor do provider na linha da lista** (opção c), útil no filtro "Todas". |

## Legenda de status

| Status | Significado |
|--------|-------------|
| ✅ DECIDIDO | Thiago respondeu; decisão registrada |
| ❓ EXPLICAR | Thiago pediu mais explicação antes de decidir — **aguardando resposta** |
| 🔎 PESQUISA | Decisão depende de pesquisa em andamento (agente rodando) |
| ⏸️ ADIADO | Thiago decidiu deixar para depois |
| ⬜ PENDENTE | Sem resposta ainda |

## Placar

- ✅ Decididas: **75** (74 do Lote 2 + P67 reclassificado no Lote 3)
- ❓ A explicar: **0**
- 🔎 Em pesquisa: **0**
- ⏸️ Adiadas: **8** (P68–P75 auditoria)
- ⬜ Pendentes: **0**

> **P67 saiu de ADIADO** no Lote 3 (virou "retrofit do tool_runner sobre o `SubprocessService` do
> plano 09") — ver Lote 3 ao final. Os 8 adiados restantes são todos de auditoria (P68–P75).

> **Lote 2 (2026-06-18)** trouxe mudanças que simplificam o MVP — destacadas com ⚠️ MUDANÇA nas
> linhas P5, P15, P29, P42, P47, P65 (e ripple em P19, P36, P43, P48).

---

## Tema A — Modelo de conversa, ciclo de vida e identidade (plano 01)

| P | Status | Decisão |
|---|--------|---------|
| **P1** | ✅ | **Stubs** (opção a): 01 cria esqueletos de `inboxes`/`assignee_user_id` sem FK; 02/03 fazem ALTER aditivo depois. |
| **P2** | ✅ | **Sempre reabrir a mesma conversa** quando o cliente volta a falar (não criar nova). Combina com P3. |
| **P3** | ✅ | **Só `open`/`closed` (resolved)** no MVP. Resolvida some do painel de abertas; nova mensagem do cliente reabre. Estado **"aguardando"** fica para o futuro. |
| **P4** | ✅ | Conversa **nasce `open`** e entra na fila; indicador de "IA ativa" mostra que o robô está atendendo. |
| **P5** | ✅ ⚠️ MUDANÇA | Cascata de IA = **IA global → inbox → conversa** (SEM nível de contato). Não precisa desligar a IA por contato. O botão de toggle age na **conversa**. `contacts.ai_enabled` **sai do gate** (aposentado ou ignorado). |
| **P6** | ✅ | `display_id` **global por conta via tabela-contador** (`UPDATE … RETURNING n` atômico). Pesquisa confirmou: Chatwoot usa SEQUENCE-por-conta (só Postgres) via trigger; como precisamos de **SQLite+Postgres com o mesmo código**, a tabela-contador é a única portável e concorrência-safe (sem o race do `MAX()+1`). Escopo global (não por inbox), como Chatwoot/Zendesk/Intercom. |
| **P7** | ✅ | Auto-resolução por inatividade: **desligada**, fica como extra para depois. |
| **P8** | ✅ | Grupos viram **conversa normal**, porém com **identificação visual de que é grupo** (badge). NÃO ocultar grupos das filas (diverge da recomendação original). |
| **P9** | ✅ | Modelo **Chatwoot de membership**: atendente só vê/atua nas inboxes em que é membro; fora delas, não vê nada. Dentro, conforme permissão. |
| **P10** | ✅ | **Archive ortogonal ao status** (opção b): dá para arquivar conversas mesmo abertas. `is_archived` é flag independente do status. |
| **P11** | ✅ | Merge de contatos: **fora do MVP**, previsto para o futuro. Schema deixa o caminho aberto. |
| **P12** | ✅ | `source_id` = **JID + LID** (opção b, estilo Evolution), priorizando estabilidade. Guardar ambos. *Decidir detalhe de qual é a chave primária na implementação.* |

## Tema B — Canais, providers, runtime de subprocesso (planos 02 e 09)

| P | Status | Decisão |
|---|--------|---------|
| **P13** | ✅ | Rotear pela combinação **`device_id` do payload + path por canal** (opção a); confirmar nos testes que `device_id` vem em todos os tipos de evento. |
| **P14** | ✅ | MVP só **Opção A** (1 processo, N devices); coluna `gowa_isolation` já no schema para habilitar dedicated depois. |
| **P15** | ✅ ⚠️ MUDANÇA | **MVP sem cifragem** para simplificar — tokens/credenciais ficam em **texto puro** no banco por enquanto. Sem chave mestra. ⚠️ Risco aceito conscientemente; **revisitar e cifrar** antes de produção séria. (Anula a chave mestra do P15 original.) |
| **P16** | ✅ | Mídia da Cloud API: **baixar e cachear** em `statics/media/` (opção a). |
| **P17** | ✅ | Janela 24h: **bloquear texto livre + template fora da janela** (opção a), mas **depois** que o principal funcionar. |
| **P18** | ✅ | Idempotência por índice único `(channel_id, external_msg_id)`. OK. |
| **P19** | ✅ | Templates Cloud API: **upload pelo painel** desejado + sincronizar **sob demanda** (quando alguém abrir/buscar na API), **sem** sync periódico em segundo plano. *(Ripple P15: tokens do WABA **sem cifragem** no MVP.)* |
| **P20** | ✅ | **Capturar o número após o login** e salvar em `channels` (opção a); aceitar vazio até o 1º login. |
| **P21** | ✅ | Contrato de provider **só declarativo** via `entry.channels`/`entry.lifecycle` (opção a); sem registro imperativo no MVP. |
| **P22** | ✅ | Disable de plugin: **teardown aguardado antes do `os._exit`** (opção a). Hot-unload fica para o futuro. *(= P25.)* |
| **P23** | ✅ | Bootstrap especial no upgrade garante `gowa` presente + `enabled=1`, preservando a sessão (opção a). |
| **P24** | ✅ | Provider lê/grava tabelas de canal via **`ctx.channel_registry`** (opção a). |
| **P25** | ✅ | **Restart-do-processo no MVP** (opção A). Mesma decisão do P22. |
| **P26** | ✅ | Supervisor e subprocesso vivem em **novo pacote `runtime/`** (opção B). Atualizar a árvore no CLAUDE.md. |
| **P27** | ✅ | Supervisor usa **`task.cancel()` nativo** (opção A); `state.stop_event` global mantido só por compat na transição. |
| **P28** | ✅ | **Emitir eventos no bus** (`task.crashed`, `subprocess.crashed/restarted`) — opção B, só na transição. |
| **P29** | ✅ ⚠️ MUDANÇA | **Só Linux/Docker por enquanto** — Windows fora do escopo do Pro. Die-with-parent via **`PR_SET_PDEATHSIG`** (Linux). Job Object do Windows **adiado** (implementar só se voltar a empacotar EXE). Stale-kill no boot continua valendo. |
| **P30** | ✅ | Health de tasks/subprocessos: **só memória** no MVP (opção A). |
| **P31** | ✅ | Teardown: **timeout fixo (~10s)** e seguir (opção A). |

## Tema C — RBAC, usuários, sessões (plano 03)

| P | Status | Decisão |
|---|--------|---------|
| **P32** | ✅ | GESTOR **atende** conversas (opção a); quem não atende é só não receber membership de inbox. |
| **P33** | ✅ | `users.manage` **exclusivo do admin** (opção a). |
| **P34** | ✅ | Update **força criar 1º admin** (opção a) + env headless para Docker. |
| **P35** | ✅ | Sessão via **Bearer token opaco** no MVP (opção a); cookie HttpOnly depois. *(Aside sobre permissões granulares tratado no plano 03 — catálogo de permissões já é granular; ver nota abaixo.)* |
| **P36** | ✅ | Quick replies **globais, atendente edita** (opção a). *(Ripple P42: sem escopo no MVP — só lista global. = P43.)* |
| **P37** | ✅ | Recuperação de senha: **admin reseta** numa tela simples (opção a); SMTP no futuro. |
| **P38** | ✅ | `inbox_members`: **bloquear scoping até o plano 01** (opção b); se 03 vier antes, stub + FK depois. |
| **P39** | ✅ | Sessão **expira em 14 dias**, mas **editável** (config no banco/tela). Sem refresh, sem limite simultâneo no MVP; "logout-all" disponível. |
| **P40** | ✅ | **1 papel por usuário** no MVP (UI single). Schema pode permanecer N:N (custo zero) para expandir depois. |

## Tema D — Respostas rápidas (plano 04)

| P | Status | Decisão |
|---|--------|---------|
| **P41** | ✅ | **Bloquear `short_code` duplicado** (não deixar criar atalhos com nome igual). Unicidade **global** enforced. |
| **P42** | ✅ ⚠️ MUDANÇA | **Quick replies SEM escopo no MVP** — lista **global única**, resolvida por um **`WHERE` simples**. Sem colunas `scope`/`inbox_id`/`user_id`. `short_code` com **UNIQUE global** (sem índice parcial). Escopo por inbox/usuário fica para o futuro. |
| **P43** | ✅ | **Atendente também cria/edita** (a lista é global). Gate por `quickreply.manage`. *(Ripple P42: sem escopo a moderar.)* |
| **P44** | ✅ | **Cache no client + evento** `whatsbot:quick-replies-changed` (opção b); refresh por foco se multi-aba incomodar. |
| **P45** | ✅ | Validação de `short_code` no **front-end**: minúsculas, **sem espaços/acentos**, **não começar com `/`**, mostrando o erro. Menu abre só com match (comportamento Chatwoot/Slack). |
| **P46** | ✅ | **Só texto** no começo; evoluir para mídia depois. (Reservar colunas `media_*` nullable na 1ª migration é opcional.) |
| **P47** | ✅ ⚠️ MUDANÇA | **MVP sem variáveis** — respostas rápidas são **texto puro**, sem `{{...}}`. Variáveis (`{{contact.name}}` etc.) ficam para uma fase futura. (Simplifica: sem parser de variáveis, sem preview.) |
| **P48** | ✅ | **Esconder** opções sem permissão (não mostrá-las travadas). Uma só tela, gateada por `quickreply.manage`. *(Ripple P42: sem opções de escopo a gatear.)* |

## Tema E — Atributos personalizados (plano 05)

| P | Status | Decisão |
|---|--------|---------|
| **P49** | ✅ | Exclusão de definição: **soft-delete** (opção c) + ação opcional de limpar órfãos. |
| **P50** | ✅ | Keys desconhecidas no PUT: **erro 400** (opção a). |
| **P51** | ✅ | **Permitir mesma key** em contact e conversation (opção a), expondo escopo na UI. |
| **P52** | ✅ | `number` **cru** no MVP (opção a); currency/percent depois. |
| **P53** | ✅ | IA grava **todos** os atributos no MVP (opção a); flag `writable_by_ai` planejada. |
| **P54** | ✅ | **Conviver** (opção a): campos fixos (`profession`/`company`/`address`) continuam colunas; custom são aditivos. **Precisamos de atributos tanto de CONTATO quanto de CONVERSA** (igual Chatwoot). |
| **P55** | ✅ | Índice de expressão **só para campos `filterable`** (opção a), decidido junto com o plano 08. |
| **P56** | ✅ | Timestamps em **epoch Float** (consistência com o projeto). |

## Tema F — Motor multi-agente / code-in-DB (plano 06)

| P | Status | Decisão |
|---|--------|---------|
| **P57** | ✅ | **1 worker do uvicorn** no MVP (invalidação por evento + cache curto bastam). Reavaliar com mecanismo de sincronização se a carga exigir multi-worker. |
| **P58** | ✅ | Histórico **montado das `messages`** (opção b), uma fonte de verdade. (Painel já mostra as tools chamadas pela IA.) |
| **P59** | ✅ | `ai_variables` em **tabela dedicada** (opção a). |
| **P60** | ✅ | Agente↔inbox via **coluna `default_agent_key`** (opção a); handoff cobre multi-agente na conversa. |
| **P61** | ✅ | Colisão de nome de tool: **código > plugin > banco** (opção a), com warning + badge. |
| **P62** | ✅ | Runner code-in-DB: **subprocess + RLIMIT + timeout** (opção a) no dia-1. Thiago vai **testar no Docker/Linux**. *(Em aberto: o container Coolify roda com privilégios para seccomp? — confirmar nos testes.)* |
| **P63** | ✅ | IA criando tools: **gate humano** (opção a) — nasce `pending` até ADM aprovar. |
| **P64** | ✅ | **Structured output via Pydantic `output_schema` do Agno** (opção a) — é exatamente o que o gerenciamento-ia faz (`LLMResponse{ mensagens_para_usuario: list[str], private_message: bool }`, com `silent_output` controlado por código, não pelo LLM). **Multi-provider confirmado** via OpenRouter: formato `provider/modelo` (`openai/...`, `google/...`, `anthropic/...`), com auto-detecção de prefixo e tuning por agente (`temperature_<agente>` > global). Ver detalhe abaixo. |
| **P65** | ✅ ⚠️ MUDANÇA | **Ir direto para o Agno desde o início**, se possível — sem período longo de coexistência com o handler legado. Construir o motor sobre o Agno desde a primeira fase; manter o legado só como fallback mínimo/curtíssimo se algo não tiver paridade. Reduz código duplicado. |
| **P66** | ✅ | **Não bloquear dependências** no MVP (sem allowlist) para facilitar. ⚠️ Aumenta a superfície de risco do code-in-DB — revisitar no endurecimento. |
| **P67** | ✅ (Lote 3) | **Retrofit**: reusar o `SubprocessService` (planos 02/09) para o tool_runner code-in-DB. Saiu de ADIADO — o runner já existe in-process; migrar para subprocesso isolado quando o plano 09 entregar o serviço (Onda do retrofit). Ver Lote 3. |

## Tema G — Auditoria e LGPD (plano 07)

> ⏸️ **Thiago adiou todo o tema de auditoria** — será a última coisa a ser feita (se for feita).
> P68–P75 ficam em aberto.

| P | Status | Decisão |
|---|--------|---------|
| **P68** | ⏸️ ADIADO | JSONB vs TEXT para diffs. |
| **P69** | ⏸️ ADIADO | Propagação do ator ao handler de bus. |
| **P70** | ⏸️ ADIADO | `actor_type = ai`. |
| **P71** | ⏸️ ADIADO | Auditar `message.sent/received`. |
| **P72** | ⏸️ ADIADO | LGPD / direito à eliminação. |
| **P73** | ⏸️ ADIADO | Auditar acesso à auditoria + duplicidade rota×bus. |
| **P74** | ⏸️ ADIADO | Imutabilidade no SQLite. |
| **P75** | ⏸️ ADIADO | Numeração da migration de auditoria. |

## Tema H — Filtros e views salvas (plano 08)

| P | Status | Decisão |
|---|--------|---------|
| **P76** | ✅ | Filtros canônicos em **`/api/conversations`** (opção a); `/api/contacts` só `q`/`archived` legado. |
| **P77** | ✅ | **Reusar a tag do contato** para a conversa (opção a); `conversation_tags` só se precisar no futuro. |
| **P78** | ✅ | AND/OR **plano** no MVP (opção a); aninhado só com demanda. |
| **P79** | ✅ | Views salvas: escopo **`user`/`global`** (opção a); `team`/`inbox` quando o 03 entregar teams. |
| **P80** | ✅ | **Página 30 + scroll infinito** (opção a), cursor opaco, teto ~100. |
| **P81** | ✅ | `archived`: **manter toggle dedicado** (opção a) + expor como dimensão no filter-schema. **Ordenação esclarecida (FQ4): por última atividade, mais recente no topo** — a conversa sobe ao chegar mensagem. Comentário anterior retirado. |
| **P82** | ✅ | Encadeamento **linear** das revisões Alembic (opção a): cada migration aponta para o head real no momento de implementar. Sem branches. |
| **P83** | ✅ | Filtros como tool do LLM: **fora de escopo** agora (opção a); ideia registrada. |

---

## Notas e pontos a esclarecer (anexos às respostas)

- **P35 (aside sobre permissões granulares):** o catálogo de permissões do plano 03 já é granular
  (ex.: `conversation.read`, `conversation.assign`, `users.manage`, `quickreply.manage`, etc.).
  Mais granularidade no futuro = adicionar entradas no catálogo, sem reprojeto. Confirmado que o
  modelo escolhido (RBAC simples por papel) suporta crescer.
- **P81 (ordenação da lista):** o comentário "as conversas mais novas que mensagem forem chegando
  não subir nas conversas" precisa de esclarecimento — o padrão de inbox é **ordenar por última
  mensagem (mais recente no topo)**. Você quer o contrário (ordem fixa por chegada / não reordenar
  ao chegar mensagem)? Isso muda a UX da lista. **A confirmar.**
- **P12 (JID+LID):** escolha mais robusta porém mais complexa que número normalizado. Impacta o
  backfill (precisa do JID de cada contato existente). Detalhar na implementação do plano 01.

---

## Perguntas que ainda precisam da sua decisão

**Nenhuma** das perguntas funcionais — todas as 74 estão decididas.

Em aberto **só a auditoria** (⏸️ adiada por escolha sua, será a última coisa): **P67, P68–P75**.
Quando for implementar auditoria, retomamos essas 9.

## Mudanças que o Lote 2 trouxe (impacto nos planos)

Estas decisões simplificam o MVP e exigem revisão dos planos correspondentes:

1. **P5** → plano **01**: cascata de IA sem nível de contato (global → inbox → conversa);
   `contacts.ai_enabled` sai do gate.
2. **P15** → plano **02**: remover cifragem de tokens do MVP (texto puro; sem chave mestra). Anula
   partes de §cifragem do plano 02.
3. **P29** → planos **02/09**: só Linux/Docker; `PR_SET_PDEATHSIG`; remover trabalho de Job
   Object/Windows do escopo imediato.
4. **P42 + P47** → plano **04**: respostas rápidas viram **texto puro, global, sem escopo e sem
   variáveis**. Remove a fase de escopo e a fase de variáveis. Grande simplificação.
5. **P65** → plano **06**: ir direto para o Agno desde o início; remover a fase de longa coexistência
   com o legado.
6. **Banco** (decisão global) → planos **05/07**: Postgres pode ser exigido para JSONB+GIN (filtros)
   e imutabilidade de auditoria.

---

## Lote 3 — pós-implementação do motor AGNO (2026-06-19, HEAD `58586e1`)

> Decisões tomadas DEPOIS que o motor AGNO + AI engine code-in-DB já estava no código
> (`fe39af2` … `58586e1`). Sincronizam as decisões originais com a realidade implementada.
> Ver `_REAVALIACAO-relatorio.md` e `_REAVALIACAO-capability-map.md`.

**P60 (granularidade agente↔inbox) — DIREÇÃO CONFIRMADA, sem trabalho agora.**
- O commit `58586e1` removeu o multi-agente Team; sobra **`ai_agents` como fonte única de verdade**
  dos agentes (single-agent, atrás de `ai_engine_enabled`). A divergência "dois caminhos de config"
  deixou de existir.
- **Decisão:** manter `ai_agents` como fonte única. Multi-agente/handoff volta **junto com as inboxes
  (plano 01)**, via coluna `default_agent_key` na inbox (+ `active_agent_key` na conversa, se houver
  handoff), com a seleção fluindo na cascata global→inbox→conversa. **Não** recriar o caminho
  `config["agents"]`. Nada a construir antes do plano 01.

**P62 (isolamento do runner code-in-DB) — MITIGAÇÃO IMEDIATA + dívida registrada.**
- O instalador entregue executa o código do banco **IN-PROCESS** no boot, contra a decisão dia-1
  (subprocess+RLIMIT+timeout). Sem RBAC ainda, "admin-only" não é implementável.
- **Decisão (curto prazo, FEITO):** kill-switch `ai_tools_code_enabled` (default **OFF**, env
  `WHATSBOT_AI_TOOLS_CODE`) gateando o instalador em `server/app.py`; tool criada via API nasce
  `enabled=False` (gate P63 real). Documentado como dívida em `agent/ai_tool_installer.py`.
- **Decisão (médio prazo):** retrofitar o isolamento sobre o `SubprocessService` do plano 09 (Onda
  4). **P67 sai de ADIADO → vira "retrofit"**: o tool_runner já existe in-process; quando o 09
  entregar o serviço de subprocesso, migrar o instalador para ele.

**P64 (structured output via `output_schema` Pydantic) — ADIADO.**
- Não foi implementado; o split de mensagens usa parse manual. O PR #8 (`71ed713`) endureceu esse
  caminho (histórico do assistant em JSON; 1/10 → 15/15 com tools), tornando-o robusto o suficiente.
- **Decisão:** **rebaixar P64 a fase futura opcional** — não é dia-1. Reavaliar só se/quando handoff
  ou roteamento exigirem saída estruturada de verdade.

**P65 (coexistência legacy×Agno) — CUMPRIDA.** O loop de tool-calling do OpenAI foi removido; o motor
é Agno-only. Resta validar paridade em produção. Sem rota de rollback ao loop legado (aceito).
