# Plano 147 — Auditoria do core: o que o sistema faz e não registra

> **Status:** 🟡 PLANEJADO (2026-08-28) — nada implementado · **Escopo:** grande (core: ~25 arquivos; sem migration; **sem bump** de `WHATSBOT_API_VERSION` — ver §6.4)
> **Origem:** relato do operador — *"apaguei uma conversa e a ação não foi registrada na auditoria"*. A investigação partiu daí e varreu a trilha inteira.
> **Método:** 41 agentes de leitura de código com verificação adversarial por finding (cada lacuna foi entregue a um cético encarregado de **refutá-la** lendo o código) + consulta **somente-leitura** ao banco de produção pelo cofre. 148 candidatas → **125 confirmadas, 23 refutadas**. 107 confirmadas são do core e estão aqui; as 32 de plugin estão no **[plano 148](148-plano-auditoria-dos-plugins.md)**.
> **O quê/porquê:** a trilha não tem um defeito, tem **três classes de defeito**: (a) o evento é emitido e ninguém o escuta; (b) a ação não emite nada e ninguém a registra; (c) a linha é gravada mas **mente** (recurso errado, "antes" vazio, ator errado, segredo em claro). O sintoma relatado é o caso (a) mais simples de todos.
>
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima.

---

## 0 — Decisões a travar antes de executar

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 ⬜ | **A regra de escopo continua valendo**: conversa/mensagem/presença/recibo não entram na trilha. Mas **o objeto conversa não é a mensagem** — excluir, fechar, atribuir e calar uma conversa são atos administrativos e entram. | Define a fronteira da F1. `message.*`, `presence.changed`, `receipt.changed` e `channel.status_changed` continuam **fora**, travados por teste. |
| D2 ⬜ | Login/logout/falha de login **entram** na trilha. | Exige mexer na ordem do middleware (§4.2) — a rota `/api/auth/` retorna **antes** de o ator existir. É a correção mais delicada do plano. |
| D3 ⬜ | O ato de **desligar a auditoria** é auditado, e passa a exigir `audit.manage` (não `settings.manage`). | Muda quem pode fazer: hoje todo `gestor` desliga a trilha. Pode quebrar fluxo de alguém — decidir antes. |
| D4 ⬜ | Ação da **IA** que muda estado com dono é auditada com `actor_type="ai"`. | Acrescenta volume novo (4 tools). Alternativa: só as duas destrutivas (`transfer_to_human`, `set_custom_attribute`). |
| D5 ⬜ | `contact.ai_toggled` é **62 % de toda a trilha de produção** (1.321 de 2.118 linhas). Decidir se sai da allowlist, se ganha agregação, ou se fica. | Se ficar, toda linha nova entra num mar de ruído. Ver §3.3. |
| D6 ⬜ | O guard `test_audit_matrix_is_complete` **já falha hoje** (12 nomes × 21 entradas). Ele volta a ser verdade (célula + golden por evento) ou vira derivado da allowlist? | Bloqueia a F1: sem decidir, cada linha nova na allowlist piora uma falha que já existe. |

**Princípio fixo:** uma linha de auditoria **errada é pior que nenhuma** — quem filtra por conversa e recebe o id do contato tira conclusão falsa. Toda entrada nova na allowlist vem acompanhada da correção do `resource_id`.

---

## 1 — Resumo executivo

A trilha tem dois caminhos de escrita: o **automático** (um handler `*` no barramento confere cada evento contra `AUDITABLE_EVENTS` em [db/audit_actions.py](../db/audit_actions.py)) e o **direto** (`audit_listener.record(...)`, e `plugins.context.audit(...)` para plugins).

O caminho automático funciona. O problema é que a allowlist tem **21 entradas** para um catálogo de **~75 eventos**, e o caminho direto tem **exatamente 3 chamadas em todo o core** — `api_keys.py`, `webhooks_out.py` e o auto-registro do export em `audit.py`. Todo o resto do painel escreve sem deixar rastro.

O resultado, medido em produção: **10 constantes do vocabulário do core nunca foram gravadas uma única vez** porque ninguém as chama. `AuditAction.AUTH_LOGIN`, `USER_CREATE`, `USER_DELETE`, `ROLE_ASSIGN`, `AGENT_UPDATE` existem no código, aparecem na documentação, e são **letra morta**.

As correções se organizam em cinco frentes, da mais barata para a mais cara:

| Frente | O que é | Custo |
|---|---|---|
| **F1** | Eventos **já emitidos** que só precisam de uma linha na allowlist (11 famílias, incluindo o sintoma relatado) | 1 arquivo + 1 chave no listener |
| **F2** | Identidade e acesso: login, usuários, papéis, senha — hoje **zero cobertura** | ~6 arquivos, mexe na ordem do middleware |
| **F3** | Ações destrutivas e em massa que não emitem nada (excluir contato, importar CSV, limpar histórico, templates da Meta) | ~12 call sites |
| **F4** | Qualidade da linha: `resource_id` nulo, "antes" ausente ou mentiroso, credencial com diff vazio | ~10 emit sites |
| **F5** | Segredo e integridade: settings de plugin em claro (**vaza DSN do Postgres hoje**), CSV injection, gate do toggle, ator `system` indevido | ~8 pontos |

---

## 2 — Como funciona hoje (mapa verificado)

```
ação do usuário
   │
   ├─(a) emite evento no bus ──► plugins.events.emit ──► handler "*" (__core_audit__)
   │                                                        │
   │                                            AUDITABLE_EVENTS.get(nome)
   │                                                        │
   │                                             não casou ──► return  ← SILÊNCIO
   │                                                 casou ──► audit_repo.add
   │
   └─(b) não emite nada ──────────────────────────────────► SILÊNCIO
```

- **Listener**: [server/audit_listener.py:92](../server/audit_listener.py#L92) (`audit_event_handler`); o `return` mudo é a linha 103-105.
- **Ator**: `ContextVar` em [server/audit_context.py](../server/audit_context.py), setado pelo middleware em [server/app.py:715](../server/app.py#L715). Default = `system`.
- **`resource_id`**: extraído por tentativa e erro de uma tupla fixa — [server/audit_listener.py:28](../server/audit_listener.py#L28):
  ```python
  _RESOURCE_ID_KEYS = ("phone", "id", "plugin_id", "name", "tag", "key", "contact_id", "channel_id")
  ```
  **Não contém `conversation_id`.** É a armadilha central da F1.
- **Gate**: `config_repo.get("audit_enabled", True)`, relido a cada escrita; **retorna `False` em exceção** ([audit_listener.py:46](../server/audit_listener.py#L46)) — soluço de banco desliga a trilha em silêncio.

---

## 3 — A prova de produção

Consulta somente-leitura ao banco de produção em **2026-08-28** (`audit_enabled = true`, 14 usuários ativos, 15.859 atendimentos, 11 canais).

### 3.1 O que aconteceu e não foi registrado

| Ação | Aconteceu? | Linhas na trilha |
|---|---|---|
| Login / logout / falha de login | 14 usuários ativos, diariamente | **0** |
| Criar usuário | **14 usuários** criados após 13/07 | **0** |
| Editar agente de IA | **52 versões** em `ai_agents_history` | **0** |
| Editar tool | **31 versões** em `ai_tools_history` | **0** |
| Excluir conversa | o sintoma relatado | **0** |
| Excluir/restaurar canal, sessão/QR | — | **0** |
| Editar/excluir etiqueta global | 4 etiquetas cadastradas | **0** |
| Webhook de saída | — | **0** |
| Alterar configuração | 10 linhas, **paradas em 24/07** | 10 |

**Contraste que fecha o argumento:** quando a **IA** edita um agente pelo plugin `melhorias`, fica registrado (`melhorias.ai_config.agent`, 4 linhas). Quando um **humano** edita o mesmo agente pela tela `/ai/agents`, não fica nada. O plugin audita melhor que o core.

### 3.2 Total da trilha

**2.118 linhas em 46 dias.** 1.937 do core, 181 de plugins — mas **48 ações distintas**, das quais 31 vêm de plugins e só 17 do core.

### 3.3 O ruído (D5)

| Ação | Linhas | % da trilha | Tem "antes"? |
|---|---|---|---|
| `contact.toggle_ai` | 1.321 | **62,4 %** | não (1.321/1.321 sem `before`) |
| `contact.update` | 195 | 9,2 % | não |
| `contact.tagged` | 193 | 9,1 % | não |
| *todo o resto* | 409 | 19,3 % | — |

Oito em cada dez linhas da trilha são o mesmo punhado de gestos de contato, **sem diff**. Acrescentar as lacunas da F1–F3 sem tratar isso entrega uma tela ainda menos utilizável.

---

## 4 — As lacunas (139 confirmadas → 107 do core, agrupadas)

Cada item traz o `arquivo:linha` verificado. IDs entre colchetes remetem ao relatório da investigação.

### 4.1 F1 — Só falta a linha na allowlist (evento **já é emitido**)

| # | Ação do usuário | Evento emitido em | Sev. |
|---|---|---|---|
| 1 | **Excluir um atendimento** (lixeira do cabeçalho) — apaga a conversa, todas as mensagens e os vínculos de etiqueta | `conversation.deleted` — [conversation_service.py:366](../app/services/conversation_service.py#L366) | **alta** |
| 2 | **Resolver / reabrir** um atendimento | `conversation.status_changed` — [:264](../app/services/conversation_service.py#L264) | **alta** |
| 3 | **Atribuir / transferir / desatribuir** (e `assign-me`, `assign-agent`, e a v1) | `conversation.assigned` — [:503](../app/services/conversation_service.py#L503) | **alta** |
| 4 | **Ligar/desligar a IA de uma conversa** — o gesto que cala ou solta o bot | `conversation.ai_toggled` — [:648](../app/services/conversation_service.py#L648) | **alta** |
| 5 | **Salvar/excluir/reverter agente, prompt, tool ou variável** (16 rotas de `/ai/*`, inclusive **código Python que roda in-process**) | `ai.config.changed` — [ai_engine.py:64](../server/routes/ai_engine.py#L64) | **alta** |
| 6 | **Criar/editar/excluir definição de atributo customizado** (muda o modelo que todos preenchem) | `custom_attribute.*` — [custom_attributes.py:78](../server/routes/custom_attributes.py#L78) | **alta** |
| 7 | **Arquivar/desarquivar** atendimento | `conversation.archived` — [:292](../app/services/conversation_service.py#L292) | média |
| 8 | **Aplicar/remover etiquetas** numa conversa | `conversation.labeled` — [:183](../app/services/conversation_service.py#L183) | média |
| 9 | **Trocar o agente de IA da conversa** e editar atributos da conversa | `conversation.updated` — [:598](../app/services/conversation_service.py#L598) | média |
| 10 | **Criar/renomear/excluir etiqueta de conversa** no registro global (painel **e** `X-Api-Key`) | `conversation_label.*` — [conversation_labels.py:68](../server/routes/conversation_labels.py#L68) | média |
| 11 | **Arquivar/desarquivar contato** | `contact.updated` — [contacts.py:847](../server/routes/contacts.py#L847) | baixa |

⚠️ **A linha na allowlist sozinha grava uma linha ERRADA.** O payload de conversa carrega `conversation_id`, `display_id`, `contact_id` — e **não** `id`. Como `_RESOURCE_ID_KEYS` não conhece `conversation_id`, o extrator cai em `contact_id` e a tela mostra `conversation:<id do contato>`. **Pré-requisito obrigatório**: inserir `"conversation_id"` **antes** de `"contact_id"` em [audit_listener.py:28](../server/audit_listener.py#L28). Vale para as 6 famílias de conversa de uma vez. `custom_attribute.*` tem o problema irmão (grava `resource_id` NULL).

⚠️ **Armadilha do item 1 — a varredura de fantasmas.** [server/background.py:326](../server/background.py#L326) chama `conversation_service.delete` em laço a cada 10 min para conversas vazias, **pelo mesmo caminho**. Com `conversation.deleted` na allowlist, a trilha ganha ruído automático com ator `system`. Distinguir na origem (flag no payload ou `record()` explícito só na rota) — decidir na F1.

⚠️ **`ai.config.changed` grava tudo como recurso `agent`.** O payload é `{kind, key, ts}`; `kind` distingue agente/tool/variável/prompt, mas o `resource_type` é fixo. Ou se aceita (o `kind` fica no `after`) ou se deriva o tipo do `kind` — que exige um pequeno seam no listener.

### 4.2 F2 — Identidade e acesso: **zero cobertura hoje**

| # | Ação | Onde | Sev. |
|---|---|---|---|
| 12 | **Login bem-sucedido** — não há como responder "quem entrou, de que IP, quando" | [auth.py:71](../server/routes/auth.py#L71) | média |
| 13 | **Falha de login** e estouro do rate-limit — força bruta é invisível | [auth.py:62](../server/routes/auth.py#L62) | média |
| 14 | **Logout** | [auth.py:97](../server/routes/auth.py#L97) | baixa |
| 15 | **Criação do primeiro admin** (bootstrap do wizard) | [auth.py:115](../server/routes/auth.py#L115) | média |
| 16 | **Criar usuário** | [users.py:76](../server/routes/users.py#L76) | **alta** |
| 17 | **Editar / desativar usuário** | [users.py:145](../server/routes/users.py#L145) | **alta** |
| 18 | **Trocar papel/permissões** de um usuário — promover a admin é invisível | [users.py:151](../server/routes/users.py#L151) | **alta** |
| 19 | **Mudar as inboxes de um usuário pela tela de usuários** — a mesma mudança pela tela de **canais** É auditada | [users.py:167](../server/routes/users.py#L167) | **alta** |
| 20 | **Resetar a senha de outro usuário** — tomada de conta alheia, silenciosa | [users.py:176](../server/routes/users.py#L176) | **alta** |
| 21 | **Excluir usuário** | [users.py:191](../server/routes/users.py#L191) | **alta** |
| 22 | **Editar permissões de um grupo** / restaurar padrões — muda o poder de todos de uma vez | [roles.py:61](../server/routes/roles.py#L61) | **alta** |
| 23 | **Criar / excluir grupo de permissão** | [roles.py:40](../server/routes/roles.py#L40) | média |
| 24 | **Trocar a própria senha** | [account.py:59](../server/routes/account.py#L59) | média |
| 25 | **Provisionar/gravar a chave do LLM** (wizard) — credencial que gasta dinheiro | [provisioning_service.py:298](../app/services/provisioning_service.py#L298) | **alta** |
| 26 | **"Testar" a chave do LLM SALVA a chave** quando o teste passa — troca de credencial disfarçada de teste | [config.py:205](../server/routes/config.py#L205) | **alta** |
| 27 | **Excluir contato por `X-Api-Key`** (`DELETE /api/v1/contacts/{phone}`) — CASCADE em conversas e mensagens | [v1/contacts.py:101](../server/routes/v1/contacts.py#L101) | **alta** |

🚫 **A armadilha da F2 — `record()` não aceita ator.** `record()` tira ator, IP e `request_id` do `ContextVar`, e as rotas `/api/auth/` são **isentas do middleware** ([app.py:580](../server/app.py#L580)) e retornam em [app.py:657](../server/app.py#L657), **antes** do `set_current_actor` (~[app.py:715](../server/app.py#L715)). Um `record(actor_type="user", …)` ingênuo no login grava `actor_user_id=NULL`, `ip=NULL`, `request_id=NULL` — e pior: passar `actor_type="user"` quando o contexto é `system` aciona o ramo `overridden` ([audit_listener.py:71](../server/audit_listener.py#L71)) que **força `actor_user_id=None`**.
**Correção real:** dentro da rota de login, após resolver a linha do usuário, chamar `set_current_actor(ActorCtx(id=…, type="user", label=…, ip=client_ip(request), request_id=uuid4().hex))` e só então `record()`, com `reset` no `finally`. Para a falha de login, o IP é o único campo que importa — usar `ActorCtx(type="system", label=<email tentado>, ip=client_ip(request))`.
`users.py` e `roles.py` **não** são isentas: ali basta chamar `record()`.

### 4.3 F3 — Destrutivo e em massa, sem emitir nada

| # | Ação | Onde | Sev. |
|---|---|---|---|
| 28 | **Excluir contato** — CASCADE apaga conversas, mensagens, observações, tags e usage | [contact_service.py:116](../app/services/contact_service.py#L116) | **alta** |
| 29 | **Importar contatos por CSV** — cria e reescreve contatos, tags e atributos em massa | [contacts.py:515](../server/routes/contacts.py#L515) | **alta** |
| 30 | **Criar / excluir template da Meta** (por canal **e** por conversa — 4 rotas) — objeto permanente na conta WABA, com categoria que define custo | [channels.py:161](../server/routes/channels.py#L161) e [:245](../server/routes/channels.py#L245); [conversations.py:1003](../server/routes/conversations.py#L1003) | **alta** |
| 31 | **Exportar a base de contatos em CSV** — telefone, nome, e-mail, empresa, endereço, tags, atributos | [contacts.py:457](../server/routes/contacts.py#L457) | média |
| 32 | **Sandbox → "Limpar conversa"** apaga **definitivamente** o histórico do telefone; com telefone vazio, de todos os contatos em cache | [sandbox.py:446](../server/routes/sandbox.py#L446) | média |
| 33 | **Sandbox marca um número REAL** como sandbox permanentemente — dali em diante o envio do atendente **não vai ao WhatsApp** | [sandbox.py:81](../server/routes/sandbox.py#L81) | média |
| 34 | **Limpar atributos órfãos** — apaga valores de todos os contatos | [custom_attributes.py:141](../server/routes/custom_attributes.py#L141) | média |
| 35 | **Biblioteca de sons da equipe**: importar/renomear/excluir — ⚠️ renomear e excluir **não têm gate de permissão nenhum** | [sound_prefs.py:172](../server/routes/sound_prefs.py#L172) | média |
| 36 | **Marcar tudo como lido / não lido** (ação em massa na instalação) | [contacts.py:2229](../server/routes/contacts.py#L2229) | baixa |
| 37 | **Criar contato** (`check-phone?create=true`; pela v1 aparece como "contato atualizado") | [contacts.py:428](../server/routes/contacts.py#L428) | baixa |
| 38 | **CRUD de respostas rápidas** | [quick_replies.py:112](../server/routes/quick_replies.py#L112) | baixa |
| 39 | **Trocar o agente padrão da inbox** — vale para toda conversa nova daquele canal | [inboxes.py:29](../server/routes/inboxes.py#L29) | baixa |
| 40 | **Apagar histórico de execuções em massa** (`DELETE /api/executions?days=N`) | [executions.py:182](../server/routes/executions.py#L182) | baixa |
| 41 | **Limpar logs** — apaga o *segundo* registro forense do sistema | [logs.py:47](../server/routes/logs.py#L47) | baixa |
| 42 | **`repair-sequences`** (re-ancora todas as sequences do Postgres) | [admin.py:57](../server/routes/admin.py#L57) | baixa |
| 43 | **Reiniciar o servidor pela UI** (`os._exit(0)`) | [plugins.py:551](../server/routes/plugins.py#L551) | baixa |
| 44 | **Logout/reconnect legados do WhatsApp** — a mesma ação pela tela de canais É auditada | [whatsapp.py:58](../server/routes/whatsapp.py#L58) | baixa |
| 45 | **`public_base_url` se reescreve sozinha** ao abrir o painel, sem trilha | [config.py:88](../server/routes/config.py#L88) | baixa |

### 4.4 F4 — A linha existe, mas mente

| # | Defeito | Onde |
|---|---|---|
| 46 | **`resource_id` NULL em toda alteração de configuração** — impossível filtrar por chave, inclusive as sensíveis | [config.py:184](../server/routes/config.py#L184) |
| 47 | **Ligar/desligar tool grava sem dizer QUAL tool** (`resource_id` NULL) e sem valor antes/depois — só a lista de nomes de campos | [tools.py:71](../server/routes/tools.py#L71) |
| 48 | **Editar contato grava só o estado final** — `before_json` NULL, a trilha mostra como está, nunca o que mudou | [contact_service.py:109](../app/services/contact_service.py#L109) |
| 49 | **Renomear/recolorir etiqueta grava diff mentiroso** — o "antes" da cor é sempre igual ao "depois" (o snapshot pega o dict **vivo** do cache, mutado in place) | [tags.py:60](../server/routes/tags.py#L60) ← [memory.py:127](../agent/memory.py#L127) |
| 50 | **Tirar etiqueta de um contato** — a linha existe, mas não diz o que foi removido | [tags.py:138](../server/routes/tags.py#L138) |
| 51 | **Trocar credencial de canal**: `channel.update` é gravada com diff **vazio** — não dá para saber que o token mudou | [channel_service.py:804](../app/services/channel_service.py#L804) |
| 52 | **Re-parear GOWA com outro número** reescreve `account_identity`/`own_phone` — o canal passa a atender outra conta, sem linha | [channel_identity.py:83](../app/services/channel_identity.py#L83) |
| 53 | **Salvar Configurações grava linha mesmo sem mudança**, e `keys_changed` lista as chaves **enviadas**, não as alteradas | [config.py:154](../server/routes/config.py#L154) |
| 54 | **Excluir versão do histórico de prompt** (e renomear a nota) **não emite nada** — destruição do próprio registro de versões | [ai_engine.py:311](../server/routes/ai_engine.py#L311) |
| 55 | **Salvar código de tool** não registra o que mudou — e o salvamento **propaga para as tools irmãs** do mesmo módulo | [ai_engine.py:492](../server/routes/ai_engine.py#L492) |
| 56 | **`channel.duplicate_refused` sem trava de repetição** — a varredura reemite a mesma linha enquanto o provedor insistir | [channel_identity.py:96](../app/services/channel_identity.py#L96) |

### 4.5 F5 — Segredo, integridade e mecanismo

| # | Defeito | Onde | Sev. |
|---|---|---|---|
| 57 | 🔴 **Settings de plugin gravam valores EM CLARO** no `after_json`. **Confirmado em produção**: 5 linhas (ids 14, 248, 308, 522, 548; a mais antiga de **14/07/2026**) carregam o DSN do Postgres do Nexus com usuário e senha, legível por quem tem `audit.read` e exportável em CSV. Na **mesma linha**, `openrouter_api_key` está mascarado e `nexus_dsn` não — o mascaramento casa **nome exato** de chave | [plugins.py:346](../server/routes/plugins.py#L346) | **alta** |
| 58 | 🔴 **Desligar a auditoria não gera linha.** Religar, sim. O único ato que o sistema não registra é o que o cega | [config.py:153](../server/routes/config.py#L153) + [audit_listener.py:108](../server/audit_listener.py#L108) | **alta** |
| 59 | **O gate do toggle é `settings.manage`, não `audit.manage`** — todo `gestor` desliga a trilha; e quem tem `audit.manage` puro vê o botão e toma 403 | [settings.py:274](../config/settings.py#L274) e [:325](../config/settings.py#L325) | média |
| 60 | **Ação humana em rota que o plugin autentica** (`/api/plugins/<id>/public/...`) grava `actor_type='system'`, sem usuário, IP ou `request_id`. Caso vivo: "Conectar com Instagram" | [app.py:643](../server/app.py#L643) | média |
| 61 | **`filter.event.before_emit` devolvendo `None` apaga a linha de auditoria junto com o evento** — para 15 das 21 entradas da allowlist. Um plugin cega a trilha sem querer | [events.py:506](../plugins/events.py#L506) | média |
| 62 | **Soluço de banco desliga a auditoria em silêncio**: o gate devolve `False` em exceção, listener e `record()` engolem tudo em `logger.warning`, e nada na tela indica o buraco | [audit_listener.py:46](../server/audit_listener.py#L46) | baixa |
| 63 | **A poda de retenção apaga linhas da trilha sem registrar nada** — nem quantas, nem por qual retenção; `audit_retention_days` é editável | [background.py:286](../server/background.py#L286) | baixa |
| 64 | **Com a auditoria desligada, exportar continua gravando** — a tela afirma "nada novo é gravado" e mente | [audit.py:117](../server/routes/audit.py#L117) | baixa |
| 65 | **O export não registra O QUE foi exportado** (os 8 filtros são descartados) | [audit.py:120](../server/routes/audit.py#L120) | baixa |
| 66 | **CSV injection no export da trilha**: `resource_id` (nome de etiqueta, ids vindos de `plugins.context.audit`) começando com `=`/`+`/`-`/`@` vira fórmula no Excel | [audit.py:133](../server/routes/audit.py#L133) | baixa |
| 67 | **Mascaramento por igualdade exata de nome de chave** — `*_dsn`, `*_secret`, `proxy_url`, `page_id` passam em claro | [audit_repo.py](../db/repositories/audit_repo.py) | média |

### 4.6 F6 — A tela `/audit`

| # | Defeito | Onde |
|---|---|---|
| 68 | **Ação por chave de API aparece com o crachá "Sistema"** (ao lado do nome do dono — mais enganoso ainda) e não há como filtrar por ela | [AuditLog.js:45](../web/static/js/components/AuditLog.js#L45) |
| 69 | **Não há filtro por pessoa** — só por *tipo* de ator. "O que o Fulano fez?" não tem resposta na tela | [AuditLog.js:213](../web/static/js/components/AuditLog.js#L213) |
| 70 | **A procedência da chave o backend calcula e devolve; a tela joga fora** | [audit.py:74](../server/routes/audit.py#L74) |
| 71 | ⚠️ **Um gestor limitado a uma inbox lê a trilha da instalação inteira** — telefone, nome, e-mail e empresa de contatos de inboxes das quais não é membro. **Decisão de produto**, não correção mecânica: ou `audit.read` sai do `gestor`, ou a query passa a filtrar por inbox alcançável | [audit.py:63](../server/routes/audit.py#L63) |

### 4.7 F7 — A IA como ator

Quatro tools mudam estado com dono e não deixam linha, enquanto **o mesmo gesto feito por uma pessoa deixa**:

| # | Tool | O que faz | Sev. |
|---|---|---|---|
| 72 | `transfer_to_human` | desliga a IA do contato, tira atendente e agente, zera `ai_active` — direto no repo, fora do service | [transfer_to_human.py:69](../agent/tools/transfer_to_human.py#L69) · média |
| 73 | `save_contact_info` | reescreve o **nome** do contato e acrescenta observações | [save_contact_info.py:46](../agent/tools/save_contact_info.py#L46) · média |
| 74 | `set_custom_attribute` | preenche atributos de negócio (plano, CPF, cidade) e **pode trocar o escopo pedido** | [set_custom_attribute.py:97](../agent/tools/set_custom_attribute.py#L97) · média |
| 75 | `transferir_agente` | passa a conversa para outro agente de IA (handoff hub-and-spoke) | [transferir_agente.py:111](../agent/tools/transferir_agente.py#L111) · baixa |

---

## 5 — Fases de execução

> Ordem pensada para que cada fase entregue valor sozinha e não dependa da seguinte. **F0 é bloqueante.**

### F0 — Destravar os guards (bloqueante)

1. `test_audit_matrix_is_complete` ([test_audit_characterization.py:476](../tests/integration/characterization/test_audit_characterization.py#L476)) exige igualdade com **12 nomes** enquanto a allowlist tem **21** — **já falha hoje** (é uma das falhas pré-existentes conhecidas da suíte). Decidir (D6): reescrever `covered` com célula + golden para os 9 faltantes, **ou** tornar o guard derivado da allowlist.
2. Mapear os **12 goldens** de `tests/goldens/audit_*.json`: vários congelam a trilha **defeituosa** (`before=null`, `resource_id=null`) e vão falhar quando a F4 entrar. Marcar cada regeneração como intencional.
3. Confirmar que `AUDITABLE_EVENTS` **não** está na superfície versionada — só `PLUGIN_ACTION_RE` está em `tests/goldens/plugin_api_surface.json`. **Logo: acrescentar entradas na allowlist NÃO exige bump de `WHATSBOT_API_VERSION`.**

**Status de execução:** ⬜

### F1 — A allowlist e o `resource_id` (resolve o sintoma relatado)

1. `"conversation_id"` em `_RESOURCE_ID_KEYS`, **antes** de `"contact_id"`.
2. Constantes novas em `AuditAction` (`CONVERSATION_DELETE`, `CONVERSATION_STATUS`, `CONVERSATION_ASSIGN`, `CONVERSATION_AI`, …) + as 11 famílias do §4.1 na allowlist.
3. `_audit_before` nos emit sites de conversa (o objeto é destruído: `display_id`, `contact_phone`, `inbox_id` precisam ir no "antes", senão a linha não diz o que sumiu).
4. Distinguir a varredura de fantasmas do clique da lixeira.
5. `resource_id` para `custom_attribute.*`.

**Status de execução:** ⬜

### F2 — Identidade e acesso

`set_current_actor` dentro da rota de login (com `reset` no `finally`) → `record()` para login/falha/logout/bootstrap. `record()` direto em `users.py`, `roles.py`, `account.py`. Nunca gravar `password_hash` no `after`.

**Status de execução:** ⬜

### F3 — Destrutivo e em massa

Os 18 call sites do §4.3. Regra: snapshot do `before` **antes** da escrita, `record()` **depois** do sucesso, **uma linha por gesto** (não por registro tocado — a importação de CSV grava uma linha com o resumo, não 3.000).

**Status de execução:** ⬜

### F4 — Qualidade da linha

`resource_id` em config e tool override; `before` real em contato/etiquetas; **cópia** do dict no snapshot da etiqueta (o bug de aliasing do item 49); `keys_changed` = chaves realmente alteradas; diff de credencial como `{"trocada": True}`.

**Status de execução:** ⬜

### F5 — Segredo e mecanismo

Prioridade no item 57 (vazamento ativo): derivar a máscara do **schema** da Settings do plugin (campos `format: "password"`) no emit site, mais mascaramento por **sufixo/substring** no `_sanitize`. Depois: auditar o desligamento da trilha, corrigir o gate, o CSV do export, o ator das rotas de plugin.

**Status de execução:** ⬜

### F6 — Tela e ruído

Filtro por pessoa e por chave de API; crachá correto; e a decisão D5 sobre `contact.ai_toggled`.

**Status de execução:** ⬜

### F7 — A IA como ator

As 4 tools, com `actor_type="ai"`.

**Status de execução:** ⬜

---

## 6 — Riscos e armadilhas

| # | Armadilha | Por que morde |
|---|---|---|
| R1 | Pôr `conversation.deleted` na allowlist **sem** mexer em `_RESOURCE_ID_KEYS` | Grava `resource_id` = id do **contato**. Uma linha errada é pior que nenhuma |
| R2 | Corrigir a F1 sem tratar a varredura de fantasmas | A trilha ganha exclusões automáticas de 10 em 10 minutos |
| R3 | `record(actor_type="user", …)` numa rota isenta do middleware | O ramo `overridden` **zera** `actor_user_id`. O login fica sem dono |
| R4 | Regenerar os goldens em bloco para "ficar verde" | Alguns congelam o defeito de propósito; regenerar tudo apaga a evidência do que mudou |
| R5 | Auditar por **registro tocado** em ações de massa | Importar 3.000 contatos = 3.000 linhas. A regra é uma linha por gesto |
| R6 | Confiar no mascaramento do `audit_repo` como licença | Ele casa **nome exato**. `nexus_dsn` passou e está em produção agora |
| R7 | Achar que a trilha estava desligada | `audit_enabled = true` em produção. Toda ausência medida é lacuna de cobertura |

**6.4 — Sem bump de API.** As entradas da allowlist não tocam `KNOWN_EVENTS` (os eventos já existem) nem a superfície do golden. Se alguma fase precisar de um **nome de evento novo**, aí sim: MINOR em [plugins/semver.py](../plugins/semver.py) + changelog + `UPDATE_PLUGIN_API_SURFACE=1`, no mesmo commit do call site.

---

## 7 — Testes

- Estender `tests/integration/characterization/test_audit_characterization.py` com uma célula por família nova (o guard do §F0 exige).
- **Manter verdes** `test_audit_ignores_message_traffic` e `test_audit_message_events_stay_out_of_allowlist` — são o cinto que garante que a D1 não foi violada.
- Teste novo de completude: varrer as rotas de escrita e exigir emit auditável ou `record()`. **Hoje não existe nada assim** — é o motivo de a trilha ter podido apodrecer em silêncio.
- Teste de mascaramento com um campo `format: "password"` de plugin.
- Teste do ator no login (`actor_user_id`, `ip` e `request_id` preenchidos).

---

## 8 — Documentação

- `CLAUDE.md`: até 2 linhas na seção de auditoria — a regra de que **conversa como objeto entra, mensagem não**, e o aviso do `resource_id`.
- [docs/PLUGINS_AUDITAVEIS.md](../docs/PLUGINS_AUDITAVEIS.md): a seção "Conversa NUNCA entra na trilha" precisa distinguir **mensagem** (fora) de **objeto conversa** (dentro) — hoje a redação induz ao erro que causou o sintoma.
- [docs/API_REST.md](../docs/API_REST.md): registrar que a fachada `/api/v1` passa a auditar.
