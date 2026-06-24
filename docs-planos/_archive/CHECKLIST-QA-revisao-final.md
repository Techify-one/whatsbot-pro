# ✅ Checklist de Revisão Final — WhatsBot Pro

> **O que é isto:** roteiro de teste manual para validar tudo que foi construído no salto
> "WhatsBot Pro" (planos 01–12). Cada item tem um título claro, uma frase do que é, e os
> passos concretos que o operador faz na tela. Gerado cruzando os planos em `docs-planos/`
> com o **código-fonte real** (cada feature foi confirmada no código antes de entrar aqui).
>
> **Como usar:** vá marcando `[x]`. Itens em **⚠️ Não testar** são features adiadas/fora do
> MVP — não perca tempo com elas. A ordem segue um fluxo natural de QA (acesso → conversas →
> features → infra).
>
> **Antes de começar (pré-requisitos):**
> - [ ] Ambiente de teste no ar e conectado ao WhatsApp (`/api/status` → `connected: true`).
> - [ ] Banco recém-migrado (`alembic upgrade head` rodou no boot sem erro).
> - [ ] Ter pelo menos: 1 usuário **admin**, 1 **gestor** e 1 **atendente** para os testes de permissão.
> - [ ] Um número de WhatsApp real para mandar/receber mensagens de teste.
> - [ ] Testar **sempre** em modo claro **e** modo escuro (regra do projeto).
> - [ ] Dica: ao testar mudanças de tela, dar **um** Ctrl+Shift+R (hard refresh) na 1ª vez para furar cache de módulo.

---

## 1. 🔐 Acesso, Usuários e Permissões (RBAC)

> Sistema de login multiusuário com papéis (admin / gestor / atendente) e permissões por ação.
> É a base de tudo — teste primeiro, porque várias telas só aparecem conforme a permissão.

### Primeiro acesso e login
- [ ] **Criar o primeiro administrador (banco vazio):** com nenhum usuário cadastrado, a tela de login deve oferecer "Crie o administrador para começar". Preencher email, nome e senha (≥8) → deve entrar já logado.
- [ ] **Login com email + senha:** sair e entrar de novo com o admin → cai no painel; o token fica salvo (sessão sobrevive a um F5).
- [ ] **Logout encerra a sessão:** clicar "Sair" → volta ao login; reutilizar o token antigo deve dar 401 (sessão invalidada no servidor).
- [ ] **Senha guardada com segurança:** confirmar (no banco) que `password_hash` começa com `$argon2id$` e que a API **nunca** devolve o hash em `GET /api/users`.

### Gestão de usuários (tela /usuarios — admin)
- [ ] **Criar usuário com papel:** "+ Novo" → email, nome, senha, papel "Atendente" → salvar → ele aparece na lista e consegue logar.
- [ ] **Editar usuário:** mudar o nome e trocar o papel para "Gestor" → muda na lista.
- [ ] **Resetar senha de um usuário:** botão "Resetar senha" → definir nova → o usuário loga com a nova senha (não há email/SMTP; admin define direto).
- [ ] **Desativar usuário:** desligar "Ativo" → esse usuário não consegue mais logar.
- [ ] **Excluir usuário comum:** deletar um atendente → some da lista.
- [ ] **Proteção do último admin:** com só 1 admin, tentar desativá-lo/excluí-lo → bloqueado com aviso "não é possível remover o último administrador".

### Papéis e permissões
- [ ] **Ver papéis do sistema:** aba "Papéis" → admin, gestor e atendente aparecem com suas permissões marcadas.
- [ ] **Criar papel customizado:** "+ Novo papel" (ex.: "supervisor"), marcar permissões → salvar → atribuível a usuários.
- [ ] **Editar e excluir papel customizado:** mudar permissões reflete em quem tem o papel; excluir remove o papel.
- [ ] **Papel de sistema é protegido:** tentar excluir "Gestor" → bloqueado; "Resetar para padrão" volta às permissões originais.

### Testes negativos (o coração do RBAC)
- [ ] **Atendente é barrado em ação de admin:** logado como atendente, tentar salvar Configurações (`PUT /api/config`) → **403 / Permissão negada**.
- [ ] **Menu esconde o que não pode (não desabilita):** atendente **não vê** os itens Usuários, Canais, Atributos, Configurações no menu da engrenagem; admin vê tudo.
- [ ] **Admin acessa tudo:** logado como admin, todas as telas e ações funcionam.

**⚠️ Não testar (adiado):** recuperação de senha por email/SMTP; token em cookie HttpOnly (hoje é localStorage); múltiplos papéis por usuário na UI (schema pronto, UI escolhe 1); restrição de atendente "só vê suas inboxes" (depende de membership de inbox — ainda libera tudo).

---

## 2. 📥 Inbox e Conversas

> Mudança estrutural: o atendimento agora é organizado em **conversas** (threads com status,
> atendente e número sequencial), não mais em contatos soltos. É o núcleo do produto.

### Ciclo de vida da conversa
- [ ] **Conversa nasce de uma mensagem recebida:** mandar mensagem de um número novo → surge uma conversa com número sequencial visível (#1, #2…) e a mensagem dentro dela.
- [ ] **Resolver e reabrir manualmente:** abrir conversa → "Resolver" → sai de "Abertas", entra em "Resolvidas"; "Reabrir" → volta para "Abertas".
- [ ] **Reabertura automática:** resolver uma conversa e depois o **cliente manda nova mensagem** → ela reabre sozinha com o **mesmo número** (não cria conversa nova) e sobe ao topo.
- [ ] **Arquivar é independente de resolver:** arquivar uma conversa **aberta** → ela some da lista; ligar o filtro "Arquivadas" → reaparece; desarquivar → volta ao topo das abertas.

### Filas e organização
- [ ] **Abas de status filtram certo:** "Abertas" (só abertas), "Minhas" (atribuídas a mim), "Não atribuídas" (sem dono), "Resolvidas" (fechadas).
- [ ] **Ordenação por atividade:** receber/enviar mensagem em qualquer conversa → ela pula para o topo da lista.
- [ ] **Grupos aparecem marcados:** conversas de grupo exibem um ícone de grupo (não são escondidas).

### Atribuição e IA por conversa
- [ ] **Assumir e transferir:** "Atribuir a mim" coloca seu nome no cabeçalho; "Transferir" passa para outro usuário; o contador de "Não atribuídas" desce.
- [ ] **Ligar/desligar IA por conversa:** o toggle de IA no cabeçalho vale **só naquela conversa** — desligar e mandar mensagem → IA não responde; ligar → volta a responder.
- [ ] **Cascata de IA (3 níveis):** a IA só responde se IA global **e** IA da conversa estiverem ligadas. (O antigo liga/desliga "por contato" não manda mais no gate — confirmar que desligar a IA da conversa é o que conta.)
- [ ] **Tempo real (WebSocket):** abrir o painel em 2 abas → resolver/atribuir numa aba reflete na outra **sem recarregar**.

**⚠️ Não testar (adiado):** estados "pendente"/"aguardando" e "soneca" (snooze); auto-resolução por inatividade; fusão de contatos; rail lateral de múltiplas inboxes (só aparece com ≥2 canais); prioridade da conversa.

---

## 3. 💬 Avisos de Sistema no Chat

> Eventos do atendimento (atribuir, taguear, resolver, ligar IA…) viram um **cartão central**
> no fio da conversa — só no painel, **nunca** vão pro WhatsApp do cliente. Dá rastro visual de
> quem fez o quê. Controlados por 4 grupos de liga/desliga em Configurações.

### Os avisos aparecem (com o autor)
- [ ] **Atribuição:** atribuir / assumir / remover atribuição → cartão "Fulano atribuiu a conversa para…", "Você assumiu…", "Fulano removeu a atribuição".
- [ ] **Tags:** adicionar/remover tag de um contato → um cartão **por tag** ("Fulano adicionou a tag X").
- [ ] **Status e arquivo:** resolver, reabrir, arquivar, desarquivar → cartão correspondente.
- [ ] **IA (conversa e contato):** pausar/reativar a IA → "Fulano pausou/reativou a IA"; vale tanto o toggle da conversa quanto o do contato.
- [ ] **Trocar agente ativo:** mudar o agente da conversa → "Fulano mudou o agente ativo para…".
- [ ] **Definir atributo:** preencher um atributo → "Fulano definiu X como Y"; salvar vários de uma vez → um cartão agregado ("atualizou N atributos").

### Avisos automáticos (sem ação de operador)
- [ ] **Conversa iniciada:** contato novo → cartão "💬 Conversa #N iniciada".
- [ ] **Reabertura automática:** cliente reabre conversa fechada → cartão "🔄 Conversa reaberta automaticamente".
- [ ] **IA assumiu (1× por conversa):** 1ª resposta da IA numa conversa → cartão "🤖 A IA assumiu o atendimento", e **só uma vez** (mandar mais mensagens não repete o cartão).

### Controles e garantias
- [ ] **Toggles em Configurações:** seção "Avisos de sistema no chat" tem os grupos (Atribuição, Tags, Status, IA). Desligar um grupo → aquele aviso **deixa de ser gerado** (nem grava no banco); religar → volta a gerar. Estado persiste após F5.
- [ ] **Não vaza para o cliente:** confirmar que nenhum cartão chega como mensagem de WhatsApp.
- [ ] **Não polui:** o cartão **não** conta como não-lida e **não** vira o preview/última-mensagem da conversa na lista.
- [ ] **Render correto:** cartão central, fino, com emoji + texto + hora, legível em modo claro e escuro.

**⚠️ Não testar (adiado):** etiquetas **de conversa** (o formatter existe mas não há rota que emita ainda); notificações por som/push (são do plugin `notifications`); avisos por usuário (a config é global).

---

## 4. ⚡ Respostas Rápidas

> Mensagens prontas que o operador insere no chat digitando `/atalho`. Agiliza atendimento.

- [ ] **Criar resposta rápida:** tela Respostas Rápidas → "Nova" → atalho `oi-bom` + conteúdo → salvar.
- [ ] **Usar no chat com `/`:** numa conversa, digitar `/oi` → abre lista filtrada com preview → Enter insere o texto no campo **sem enviar** (operador revisa e manda).
- [ ] **Validação do atalho:** rejeita espaço/acento/maiúscula; normaliza `/OI-BOM` → `oi-bom`; bloqueia atalho duplicado.
- [ ] **Editar e excluir:** alterar conteúdo reflete na hora no autocomplete; excluir tira do `/`.
- [ ] **Limites:** atalho até 40 caracteres, conteúdo até 5000 (acima disso, erro).
- [ ] **Modo escuro:** tela e dropdown do `/` legíveis no tema escuro.

**⚠️ Não testar (adiado):** anexos/mídia em respostas rápidas; variáveis `{{...}}`; escopo por inbox/usuário (hoje é lista global única).

---

## 5. 🏷️ Atributos Personalizados

> Campos extras definidos por você (ex.: "Plano", "CPF", "Prioridade"), preenchidos por
> contato **ou** por conversa. Aparecem no painel de informações.

- [ ] **Criar definição (contato):** tela Atributos → aba Contato → "Plano", tipo Lista, opções free/premium/vip → salvar.
- [ ] **Tipos variados:** criar e usar atributos de **número** (`idade`), **data** (`data_cadastro`), **sim/não** (`vip`) e **texto** — cada um renderiza o campo certo no painel.
- [ ] **Preencher num contato:** abrir contato → painel de info → o campo novo aparece → escolher valor → salva e **persiste** (fechar e reabrir mantém).
- [ ] **Validação de lista:** tentar gravar valor fora das opções → rejeitado.
- [ ] **Atributo de conversa:** criar com escopo "Conversa" → aparece numa seção separada ("Dados desta conversa") no painel, não no contato.
- [ ] **Escopo é imutável:** ao editar uma definição, não dá pra trocar contato↔conversa.
- [ ] **IA preenche atributo:** com a IA ligada, o cliente informa um dado (ex.: CPF) → a IA grava no atributo automaticamente (tool `set_custom_attribute`).
- [ ] **Excluir definição (soft-delete):** excluir um atributo → some das telas, mas valores já gravados continuam no banco (não some histórico).
- [ ] **Modo escuro:** campos legíveis (`.wa-field`) nos dois temas.

**⚠️ Não testar (adiado):** índice GIN otimizado / filtro pesado por atributo em larga escala (cobertura básica existe via plano de Filtros).

---

## 6. 🔎 Filtros e Busca Avançada

> Refina a lista de conversas por status, atendente, inbox, período, texto e tags — com chips
> visuais removíveis.

- [ ] **Filtros básicos:** filtrar por Status, Responsável (incl. "eu"), Caixa de entrada, "atividade nas últimas 24h" → a lista responde; combinar dois faz **E** (AND).
- [ ] **Chips removíveis:** cada filtro vira um chip ("Status: Abertas ✕"); clicar no ✕ remove; "Limpar" zera tudo.
- [ ] **Busca por texto:** digitar nome/telefone/trecho de mensagem no campo de busca → lista filtra (com pequeno atraso/debounce).
- [ ] **Filtro por tags:** filtrar por uma ou várias tags (várias = "OU"); combinar com Status mantém o AND.
- [ ] **Validação:** chave/operador de filtro inválido (via API) retorna **400**, não quebra a tela.
- [ ] **Modo escuro:** barra de filtros e chips legíveis.

**⚠️ Não testar (adiado):** "views/segmentos salvos"; drawer de filtros booleanos avançados (AND/OR aninhado).

---

## 7. 📡 Canais e Multicanal

> Abstração que permite mais de um "canal" de WhatsApp. Hoje: **GOWA** (número via QR, canal
> padrão) e **WhatsApp Cloud API oficial** (via plugin). Cada canal tem sua inbox e roteia
> entrada/saída pelo provider certo.

### Gestão de canais
- [ ] **Listar canais:** tela Canais mostra o canal "default" (GOWA, conectado) e os badges de provider/status; credenciais aparecem **mascaradas** (nunca o token inteiro).
- [ ] **Criar canal Cloud API:** "+ Adicionar canal" → provider `whatsapp_cloud` → preencher Phone Number ID, Access Token, Verify Token (App Secret opcional) → salvar → aparece "desconectado".
- [ ] **Registrar webhook na Meta:** copiar a URL `…/api/webhook/whatsapp_cloud/<id>`, colar no painel da Meta com o Verify Token → handshake responde 200 e ecoa o challenge → Meta marca "Active".

### Entrada e saída por canal
- [ ] **Receber pela Cloud API:** mandar mensagem do número oficial → aparece no painel vinculada à inbox daquele canal; responder → resposta **sai pelo número oficial** (Graph API), não pelo GOWA.
- [ ] **GOWA segue normal:** mensagem via GOWA continua entrando e sendo respondida pelo canal padrão.
- [ ] **Saída vai pelo canal certo:** com 2 conversas (uma GOWA, uma Cloud), cada resposta sai pelo seu próprio provider.
- [ ] **Agrupamento (batch) por canal:** 3 mensagens seguidas no mesmo chat Cloud → IA responde **uma vez** (agrupadas).
- [ ] **Capabilities respeitadas:** "digitando…" e lógica de @menção/grupo **não** são enviados para a Cloud API (que não suporta); no GOWA continuam funcionando.
- [ ] **Erro de credencial é gracioso:** trocar o token da Cloud por um inválido → erro logado, resposta não trava o sistema, status do canal sinaliza problema.

**⚠️ Não testar (adiado):** GOWA virar plugin; **multi-número GOWA** (hoje é 1 device); janela de 24h da Cloud + fallback de template; download/cache de mídia da Cloud; rail de múltiplas inboxes na UI; plugin `channel_test` de lifecycle (depende do Runtime).

---

## 8. 🤖 Motor de IA (Agentes config-in-DB)

> Tela "Engine de IA" (menu engrenagem → /ai) para configurar agente, prompt, variáveis e até
> tools no **banco** — sem editar código. Ligado pela flag `ai_engine_enabled` (padrão
> **desligado**). **Esta é a feature mais recente — há mudanças não commitadas; teste com atenção.**

### Liga/desliga e clareza de estado
- [ ] **Badge de status:** no topo da tela, vê-se claramente "Motor de IA: **Ativo**/**Desligado**".
- [ ] **Ativar/Desativar pela tela:** botão liga/desliga a flag (`ai_engine_enabled`), persiste após F5.
- [ ] **Hot-reload do agente:** com o motor **ligado**, editar o prompt do agente `default` e mandar mensagem → a IA já responde com o novo prompt **sem restart**. Desligar o motor → volta ao prompt global antigo.

### CRUD de agentes (pendência recém-fechada)
- [ ] **Criar agente novo (era o bug principal):** aba Agentes → "+ Novo agente" → digitar identificador (slug, ex.: `suporte_n1`) + nome + prompt + modelo → "Criar" → aparece na lista.
- [ ] **Editar agente:** abrir um existente → o campo de identificador **não** aparece (modo edição); mudar nome → salva.
- [ ] **Histórico e reverter:** editar gera versão nova (v1→v2); "Histórico" lista as versões; "Reverter" volta a uma anterior.
- [ ] **Prompts e variáveis:** criar/editar prompt com placeholders `{variavel}`; criar variáveis (chave/valor); o prompt renderiza com os valores substituídos.

### Tools com código no banco + **segurança**
- [ ] **Restart do worker (pendência recém-fechada):** "Reiniciar worker" → aparece **overlay "Servidor reiniciando…"** → a página faz polling em `/health` e **recarrega sozinha** quando o worker volta (sem `SyntaxError` de módulo no console).
- [ ] **🔒 Kill-switch de RCE (CRÍTICO):** com `ai_tools_code_enabled` **desligado** (padrão), criar uma tool de código (mesmo malicioso) → ela fica `pending`, o instalador **não roda** e a IA **não executa** o código. Confirmar nos logs que nada foi instalado/executado.
- [ ] **(Em ambiente isolado) ligar o kill-switch:** com `ai_tools_code_enabled` ligado, a tool passa pelo instalador (subprocess isolado), instala dependências e muda para `ok`/`failed`; código com erro de sintaxe → `failed` com mensagem.

**⚠️ Não testar (adiado):** multi-agente **por inbox** e roteamento/handoff executável entre agentes; endurecimento extra do isolamento (seccomp/AppArmor); migração das colunas JSON para JSONB; cache de config com invalidação por evento (hoje lê do banco a cada mensagem).

---

## 9. 📜 Auditoria (trilha de ações)

> **Atenção:** o `_LEIA-PRIMEIRO.md` diz que auditoria estava *adiada*, mas o **código foi
> entregue depois** — a tela e os endpoints existem (`server/routes/audit.py`,
> `web/static/js/components/AuditLog.js`, tabela `audit_log`). Vale testar.

- [ ] **Captura automática:** alterar uma config e salvar → na tela Auditoria aparece a linha (`config.update`, recurso `config`, com o "antes/depois").
- [ ] **Cobre plugins e tags:** habilitar/desabilitar plugin, criar/editar/excluir tag, ligar/desligar IA de contato → cada um vira uma linha na trilha.
- [ ] **Tela com filtros e paginação:** Auditoria lista por data (mais recente primeiro); filtrar por tipo de ação / recurso / período; expandir uma linha mostra o diff JSON; paginação funciona.
- [ ] **Segredos mascarados:** alterar a API key e conferir que na trilha ela aparece como `***` (mascarado **no banco**, não só na tela).
- [ ] **Exportar:** baixar CSV e JSON; um filtro aplicado na tela reflete no arquivo exportado.
- [ ] **Acesso gated:** usuário sem `audit.read` recebe 403; com a permissão, vê a tela.

**Observação:** hoje o "autor" das ações aparece como **sistema** em rotas ainda não ligadas ao usuário logado — confirmar se isso é aceitável para o MVP. Imutabilidade/LGPD e purga automática ficam para depois.

---

## 10. ⚙️ Fundação de Runtime (tarefas de fundo)

> Infra interna que gerencia tarefas e subprocessos em background (GOWA, polling de status/QR,
> avatares) com reinício automático. Tem uma tela "Runtime" para observar. Boa parte é
> verificável por logs/observação, não por clique.

- [ ] **Painel Runtime:** menu engrenagem → "Runtime" (admin) → seção de **tarefas de fundo** lista ≥4 tasks core (GOWA, status, QR, avatares) em estado "running".
- [ ] **Subprocessos gerenciados:** a tela mostra o GOWA com PID e contador de reinícios.
- [ ] **GOWA morre junto com o app (Linux/Docker):** matar o processo Python → o GOWA é encerrado junto (não fica órfão); reiniciar o app não deixa **dois** GOWA rodando.
- [ ] **Lifecycle de plugin:** habilitar/desabilitar um plugin com `setup/teardown` → os hooks rodam (logs), e o desligamento **aguarda** o teardown antes de reiniciar; nenhuma tarefa/arquivo fica órfão.
- [ ] **Endpoints de estado:** `GET /api/runtime/tasks` e `/api/runtime/subprocesses` retornam o estado real em JSON.

**⚠️ Não testar (adiado):** hot-unload de plugin sem reiniciar o processo; die-with-parent no Windows (só Linux/Docker); persistência de health em tabela; rodar o code-in-DB dentro do subprocesso gerenciado (hoje tem isolamento próprio + kill-switch).

---

## 11. 🎨 Transversal — UX, Tema e Tempo Real

> Itens que valem para **todas** as telas novas — fazer uma passada final.

- [ ] **Modo escuro em todas as telas novas:** Conversas, Usuários, Canais, Atributos, Respostas Rápidas, Motor de IA, Auditoria, Runtime, Avisos de sistema → ligar o tema escuro e conferir contraste/legibilidade (sem texto branco em fundo branco, sem campo invisível).
- [ ] **Modo claro:** repetir a passada com tema claro.
- [ ] **Preferência de tema persiste:** ligar escuro → F5 → continua escuro.
- [ ] **Navegação e deep-link:** abrir conversa muda a URL (`/conversations/:id`); colar essa URL em aba nova abre direto a conversa; voltar/avançar do navegador funciona.
- [ ] **Responsivo (mobile):** em tela estreita (≈375px), lista e chat colapsam para coluna única; painel de info vira overlay; menu acessível.
- [ ] **Permissões na UI escondem (não desabilitam):** confirmar de novo, em telas variadas, que o que o usuário não pode fazer **não aparece**.

---

## 12. 🧱 Regressão e Infraestrutura (não pode quebrar)

> Garantias de que o que já funcionava continua funcionando após todo esse salto.

- [ ] **Webhook do GOWA continua aberto:** `POST /api/webhook` sem token → **não** dá 401 (recebe e processa). Quebrar isso derruba toda a recepção de mensagens.
- [ ] **Health aberto:** `GET /health` sem token → 200.
- [ ] **Fluxo ponta-a-ponta:** mandar mensagem real → ela é recebida, agrupada, a IA responde e a resposta chega no WhatsApp (o caminho clássico do produto).
- [ ] **Mídia ainda funciona:** receber áudio (transcrição), imagem (descrição), e enviar mídia pelo painel.
- [ ] **Migração do banco limpa:** subir o app contra um banco "de produção" copiado → `alembic upgrade head` sem erro; cada contato antigo virou uma conversa; mensagens antigas têm `conversation_id`.
- [ ] **Suíte de testes passa:** rodar `python tests/test_endpoints.py` (e os novos `tests/test_quick_replies_edge.py`, `tests/manual_cloud_api_test.py`) → verde.
- [ ] **Postgres opcional:** se for usar Postgres, validar a migração SQLite→Postgres (Settings → Banco) sem erro.

---

### 📌 Achados desta revisão (para decidir antes de fechar)
1. **Auditoria foi entregue** apesar de o doc dizer "adiada" — decidir se entra oficialmente no escopo do release (testar a seção 9).
2. **Motor de IA tem mudanças não commitadas** (`AgentEngine.js`, `AgentsManager.js`) — é a feature mais nova; priorizar a seção 8 e commitar quando validado.
3. **"Autor" da auditoria = sistema** em algumas rotas (ainda não amarrado ao usuário logado) — confirmar se é aceitável para o MVP.
4. **Vários "adiados"** são esperados e estão marcados em cada seção — não são bugs.
