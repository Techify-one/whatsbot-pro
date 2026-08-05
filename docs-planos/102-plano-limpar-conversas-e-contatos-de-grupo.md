# Plano 102 — Limpar as conversas e os contatos de grupo do WhatsBot

> **Status:** ✅ EXECUTADO (2026-08-05) · **Data:** 2026-08-05 · **Escopo:** pequeno (operação em produção, sem código)
> **Resultado:** 118 contatos de grupo e as 2 conversas fantasma apagados; `Equipe_01` sem `group`; badge "Não atribuídas" = 0. Pendente com o usuário: restart no Coolify + confirmação visual no painel (F3 itens 3-4).
> **Origem:** pedido do usuário após o badge "Não atribuídas 2" mostrar contagem sem nenhuma linha na lista. **Método:** leitura do código (`arquivo:linha`) + consultas SELECT no banco de produção (`whatsbot@10.8.100.5`) em 2026-08-05.
> Produção acumulou 118 contatos de grupo (`@g.us`) e 2 conversas fantasma que o painel esconde mas o contador soma. Este plano apaga os dois, na ordem segura, com backup e verificação — e fecha a torneira que os recria.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| D1 ✅ (2026-08-05) | Os grupos **não servem para nada** no WhatsBot — podem ser apagados de vez, não só arquivados | Nada de "arquivar/ocultar"; é `DELETE` real, com o CASCADE do banco |
| D2 ✅ (2026-08-05) | O canal `Disparo-Grupos` **continua existindo** — só não deve materializar conversa de grupo | Não se toca no canal nem no device GOWA; a limpeza é só de contatos/conversas |
| D3 ✅ (2026-08-05) | A investigação já rodou e está fechada (ver §2) — **não reinvestigar** a causa antes de executar | O executor começa pela F0 (backup), não por diagnóstico |
| D4 ✅ (2026-08-05) | Operação **read-only até a F2**; nenhuma escrita antes do backup verificado | F0 é 🔴 bloqueante por definição |

---

## 1. Resumo executivo

O canal `Disparo-Grupos` (gowa) nasceu em 2026-08-04 aceitando `group` no filtro de tipos de JID. Em 2026-08-05, entre 10:56:41 e 10:56:50, **118 contatos de grupo** foram materializados de uma vez. Às 11:10:43 o usuário desmarcou "Grupo / Comunidade" no canal — mas as **linhas de contato ficaram**, porque a limpeza anterior apagou conversas e protocolos, não contatos.

Depois disso, às 13:09 e 13:30, **2 conversas fantasma** nasceram: um evento de roster ("fulano saiu do grupo") grava um `system_notice`, e esse caminho **não passa pelo filtro de tipo de JID** (§2). Cada uma contém só cards painel-only, então a sidebar as esconde e o badge "Não atribuídas" as conta — o sintoma que originou este plano.

A limpeza é: apagar os 118 contatos. O `ON DELETE CASCADE` leva conversas, mensagens, vínculos de inbox, tags e não-lidas junto. **Mas limpar sem fechar a torneira é reversível** — daí a F1.

---

## 2. Como funciona hoje (mapa verificado)

| # | Fato | Onde | Consequência |
|---|---|---|---|
| 1 | O filtro `allowed_jid_types` só roda no caminho de **mensagem** — há um `if kind != "message": return` antes dele | [message_ingest_service.py:350-374](../app/services/message_ingest_service.py#L350-L374) | Evento de roster escapa do filtro |
| 2 | O evento `group_participants` é tratado num ramo próprio, **sem consultar `allowed_jid_types`** | [channel_webhook.py:510-540](../server/routes/channel_webhook.py#L510-L540) | "Fulano saiu do grupo" entra mesmo com grupos desmarcados |
| 3 | Esse ramo só grava o aviso **se o contato já existir** (`contact_repo.get_by_phone`, que é global, não escopado por canal) | [channel_webhook.py:520](../server/routes/channel_webhook.py#L520) | **É a trava**: sem contato, nada acontece. Por isso apagar resolve |
| 4 | `add_message` de role painel-only **não reabre** conversa fechada, mas **abre uma nova** quando não existe nenhuma | [memory.py:328-373](../agent/memory.py#L328-L373) (`reopen_closed` sem par `create_closed`) | Um aviso de roster vira atendimento aberto |
| 5 | O preview da sidebar ignora roles painel-only ⇒ `last_message_ts = 0` | [conversation_query.py:20-35](../db/repositories/conversation_query.py#L20-L35), [_mapping.py:103-110](../db/repositories/_mapping.py#L103-L110) | A linha some da lista |
| 6 | A lista aplica `isVisibleInSidebar`; a contagem **não** | [conversationRows.js:120-125](../web/static/js/services/conversationRows.js#L120-L125) × [conversation_repo.py:565-573](../db/repositories/conversation_repo.py#L565-L573) | "Mostrando 0 de 2" |
| 7 | `contacts` tem CASCADE para 8 tabelas | medido: `atendimentos`, `contact_inboxes`, `contact_tags`, `mentions`, `messages`, `observations`, `unread_msg_ids`, `usage` | Apagar o contato limpa tudo numa transação |
| 8 | `contact_repo.delete` é só o `DELETE` — quem limpa cache e avisa a UI é a rota | [contact_repo.py:69-72](../db/repositories/contact_repo.py#L69-L72) × [contacts.py:894-913](../server/routes/contacts.py#L894-L913) | ⚠️ SQL direto **não** dropa o cache em memória (ver §6) |

⚠️ **Gotcha que torna a F1 obrigatória:** o fato #3 é uma faca de dois gumes. Apagar os contatos hoje resolve, mas **qualquer mensagem de grupo que chegue por um canal que ainda aceite `group` recria o contato** — e o ciclo recomeça. Hoje `Equipe_01` ainda aceita.

---

## 3. Inventário medido em produção (2026-08-05, `whatsbot@10.8.100.5`)

### 3.1 Contatos de grupo

| Item | Valor |
|---|---|
| Contatos com sufixo `@g.us` | **118** (ids 15072–15189, contíguos) |
| Criados em | 2026-08-05 **10:56:41 → 10:56:50** (rajada de 9 s) |
| Vínculo em `contact_inboxes` | **118** no inbox 24 (`Disparo-Grupos` / `gowa_4kmDxc2fD6`); **2** também no inbox 17 (`numero_recuperacao` / `gowa_gjOZx4jaNS`) |
| Com nome preenchido | 0 (todos `(sem nome)`) |
| Com 0 conversas e 0 mensagens | **116** |
| Com conversa | **2** (ids 15112 e 15188) |
| Contatos `@newsletter` / `@broadcast` | **0** |
| Contato "cara de grupo" sem sufixo (`^120363`) | **0** — nenhum escapa do filtro por sufixo |

### 3.2 Conversas de grupo

| conversa | inbox | contato | status | origin | mensagens |
|---|---|---|---|---|---|
| 15448 | 17 | 15188 (`120363029152052196@g.us`) | open, não arquivada | outbound | 2 (`system_notice` + `conversation_event`) |
| 15449 | 17 | 15112 (`120363360513147440@g.us`) | open, não arquivada | outbound | 2 (idem) |

Não existe nenhuma outra conversa de grupo — nem fechada, nem arquivada.

### 3.3 Resíduo em tabelas de plugin

| Tabela | Linhas ligadas a grupo |
|---|---|
| `plugin_protocolos_protocolos` / `_atendimentos` / `_avaliacoes` / `_ai_holds` | **0** |
| `plugin_agendamento_retorno_items` · `plugin_retornos_controle` · `plugin_janela_72h_windows` | **0** |
| `plugin_melhorias_suggestions` · `plugin_vendas_ia_conversa` | **0** |
| `plugin_debug_bus_records` | **2** (ring buffer de debug — descartável, sem FK) |

**A limpeza anterior do usuário funcionou.** O que sobrou foi exatamente a camada de contatos.

### 3.4 Estado dos canais GOWA

| canal | `allowed_jid_types` | aceita grupo? |
|---|---|---|
| `gowa_4kmDxc2fD6` · Disparo-Grupos | `["person","person_lid"]` | não ✅ |
| `gowa_gjOZx4jaNS` · numero_recuperacao | `["person","person_lid"]` | não ✅ |
| **`gowa_HFZu4VpySn` · Equipe_01** | `["person","person_lid","group"]` | **sim ⚠️** |

Trilha de auditoria: `channel.create` do Disparo-Grupos em 2026-08-04 11:34:58 (ator *Erika*); `channel.update` em 2026-08-05 11:10:43 (ator *Automação*) — a edição que desmarcou grupos, **depois** da criação dos 118 contatos às 10:56.

---

## 4. Falsos positivos descartados

| Suspeita | Por que NÃO é o problema |
|---|---|
| "A contagem está com bug de SQL" | `count_tab_counts` está correto: as 2 linhas existem mesmo, com `assignee_user_id` e `active_agent_key` nulos. O que diverge é a **regra**, não a query |
| "Sobrou protocolo de grupo para limpar" | Medido: 0 linhas em todas as tabelas de protocolo (§3.3) |
| "O `Disparo-Grupos` ainda está deixando grupo entrar" | O canal já está com `group` desmarcado desde 11:10:43. As conversas de 13:09/13:30 vieram pelo ramo de roster, que ignora o filtro (§2 #1-2) |
| "As conversas fantasma nasceram no `Disparo-Grupos`" | Nasceram no **inbox 17 (`numero_recuperacao`)** — o contato é global, então um grupo materializado por um canal habilita o aviso em outro |
| "Precisa apagar contato por contato pela tela" | O CASCADE resolve em uma transação; a tela seria 118 cliques |
| "Tem contato de Canal/Status para limpar junto" | 0 contatos `@newsletter`/`@broadcast` (§3.1) |

---

## 5. Fases / Roadmap

```
WAVE 0   F0 (backup + snapshot)                        🔴 bloqueia tudo
            │
WAVE 1   F1 (fechar a torneira)  ·  F2 (apagar)        🟢 F1 e F2 são independentes entre si
            │                        [depende de: F0]
WAVE 2   F3 (verificar)                                 🔴 [depende de: F1, F2]
```

| Wave | Fase | Workstream | Paraleliza? | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | Backup + snapshot do estado | 🔴 sozinha | baixo | dump restaurável + números de §3 reconferidos |
| 1 | **F1** | Fechar a torneira (`Equipe_01`) | 🟢 | baixo | `Equipe_01` sem `group` (ou P1 decidido por manter) |
| 1 | **F2** | Apagar contatos + conversas | 🟢 | **médio** | 0 contatos `@g.us`, 0 conversas de grupo |
| 2 | **F3** | Verificar painel + banco | 🔴 sozinha | baixo | badge bate com a lista; nada órfão |

---

### F0 — Backup e snapshot (🔴 bloqueante)

**Objetivo:** poder desfazer, e ter o "antes" registrado para comparar.

**Itens:**
1. `[sequencial]` Dump do banco de produção para `~/whatsbot-backups/whatsbot-pre-plano102-<YYYYMMDD-HHMM>.dump`:
   `PGPASSWORD=… pg_dump -h 10.8.100.5 -U postgres -d whatsbot -Fc -f <arquivo>`
   Se `pg_dump` não estiver instalado, **pare** — não execute a F2 sem backup (D4).
2. `[sequencial]` Conferir que o dump não é vazio (`ls -lh`, `pg_restore -l <arquivo> | head`).
3. `[paralelo]` Re-rodar as 4 consultas de §3.1–3.3 e **anexar a saída** no Status de execução: os números podem ter mudado desde 2026-08-05 (um novo "saiu do grupo" cria mais uma conversa).
4. `[paralelo]` Salvar a lista de telefones a apagar:
   `select phone from contacts where phone like '%@g.us' order by id;`

**Pronto quando:** o dump existe, é listável por `pg_restore -l`, e os números de §3 estão reconferidos (iguais ou com o delta anotado).

#### Status de execução — Fase 0
**Estado:** ✅ Concluída (2026-08-05 17:42 UTC)
- **O que foi feito:** dump `~/whatsbot-backups/whatsbot-pre-plano102-20260805-1742.dump` (35 MB, 825 entradas TOC, `pg_restore -l` OK, servidor 15.15 / cliente 16.13). Inventário de §3 reconferido + varredura de resíduo **ampliada**.
- **Como foi feito / decisões:** a varredura de §3.3 cobria 8 tabelas; enumerei via `information_schema` **todas** as 47 colunas do banco que poderiam apontar para grupo (`contact_id`/`conversation_id`/`phone`/`chat_id`/…) e testei as 24 relevantes. Alguns `conversation_id` são `text` e outros `int` — precisou de cast `::text` dos dois lados.
- **Problemas / pendências:** nenhum. Delta vs. o plano: **total de contatos 15050**, não 15048 (2 contatos novos, não-grupo, criados entre o planejamento e a execução).
- **Verificação:** 118 contatos `@g.us` (ids 15072–15189), 2 conversas, 4 mensagens, 120 `contact_inboxes`, **115 `unread_msg_ids`** (não catalogado no plano). 0 `@newsletter`, 0 `@broadcast`, 0 JID fora do padrão `^[0-9]+@g\.us$`. Os 118 são cascas vazias: **0 com nome, 0 email, 0 profissão, 0 empresa, 0 tag, 0 fixado**. As 4 mensagens são só `system_notice` ("… saiu do grupo") + `conversation_event` ("Conversa iniciada") — nenhum conteúdo real de cliente. Resíduo em tabelas de plugin: **0 em 23 tabelas**, só `plugin_debug_bus_records` = 2 (ring buffer).

---

### F1 — Fechar a torneira (🟢 paralela com F2) [depende de: F0]

**Objetivo:** garantir que nenhum canal recrie contato de grupo depois da limpeza.

**Itens:**
1. `[sequencial]` Confirmar com o usuário a P1 (§7): o `Equipe_01` **usa** grupos de propósito?
2. `[sequencial]` Se não usar: abrir **Canais → Equipe_01 → Editar** e desmarcar "Grupo / Comunidade". Fazer pela **UI**, não por SQL — o `PUT /api/channels/{id}` invalida o cache de 30 s do `allowed_jid_types` ([message_ingest_service.py:141-146](../app/services/message_ingest_service.py#L141-L146)) e grava a trilha de auditoria; um `UPDATE` direto deixaria o cache quente por até 30 s e sem registro de quem mudou.
3. `[paralelo]` Registrar no Status de execução o `allowed_jid_types` final dos 3 canais GOWA.

⚠️ **Isto não fecha o buraco do ramo de roster** (§2 #1-2): mesmo com todos os canais sem `group`, um "saiu do grupo" volta a criar conversa **se o contato existir**. É a F2 que remove essa condição — as duas juntas é que fecham. O conserto definitivo no código é a P2 (§7).

**Pronto quando:** nenhum canal GOWA lista `group` em `allowed_jid_types` (ou a P1 foi decidida por manter, com a consequência anotada).

#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-08-05)
- **O que foi feito:** `Equipe_01` (`gowa_HFZu4VpySn`) passou de `["person","person_lid","group"]` para `["person","person_lid"]`. Os 3 canais GOWA agora estão idênticos e sem `group`.
- **Como foi feito / decisões:** **P1 respondida pelo usuário: (a) desmarcar.** A evidência que fechou a decisão: o `Equipe_01` teve **5 conversas em toda a sua história e ZERO de grupo** — ninguém atende grupo por ele. Desvio do plano: feito por **SQL, não pela UI** (o usuário optou por "eu faço tudo"), com `jsonb_set` apenas na chave `allowed_jid_types` e `WHERE … @> '["group"]'` como guarda de idempotência; as 15 chaves do sub-objeto `ai` e as demais foram conferidas intactas depois do `COMMIT`. Consequências aceitas do desvio: sem linha de auditoria `channel.update` e o cache de 30 s de `allowed_jid_types` ficou quente por meio minuto (já expirado muito antes da F2 terminar).
- **Problemas / pendências:** nenhum.
- **Verificação:** `UPDATE 1`; `jsonb_pretty` pós-commit confirma `allowed_jid_types` novo e todo o resto preservado. Estado final dos 3 canais GOWA: `Disparo-Grupos`, `Equipe_01` e `numero_recuperacao` — todos `["person","person_lid"]`.

---

### F2 — Apagar contatos e conversas de grupo (🟢 paralela com F1) [depende de: F0]

**Objetivo:** remover os 118 contatos `@g.us` e, por CASCADE, as 2 conversas e as 4 mensagens.

**Itens:**
1. `[sequencial]` **Dentro de uma transação**, na ordem: `BEGIN;` → `SELECT count(*) FROM contacts WHERE phone LIKE '%@g.us';` (deve bater com a F0) → `DELETE FROM contacts WHERE phone LIKE '%@g.us';` → conferir o `DELETE 118` → `COMMIT;`.
   O predicado é o sufixo `@g.us`, **nunca** o intervalo de ids (15072–15189): id é frágil, sufixo é o contrato de tipo de JID ([jid.py:47-55](../channels/jid.py#L47-L55)).
2. `[sequencial]` Limpar o resíduo sem FK do debug bus: `DELETE FROM plugin_debug_bus_records WHERE phone LIKE '%@g.us';` (2 linhas; opcional — é ring buffer).
3. `[sequencial]` **Reiniciar a aplicação** (redeploy/restart no Coolify). Motivo: o `agent_handler` mantém `ContactMemory` em memória por telefone, e `contact_repo.delete` sozinho não o limpa — só a rota `DELETE /api/contacts/{phone}` faz `drop_cached_contact` ([contacts.py:905](../server/routes/contacts.py#L905)). Com o cache quente, um evento de roster tentaria gravar mensagem com `contact_id` morto e tomaria erro de FK.
4. `[paralelo]` Se o executor preferir a via de API em vez de SQL (equivalente, mais lenta): `DELETE /api/contacts/{phone}` para cada telefone da lista da F0 — exige sessão com a permissão `contact.delete` e já cuida do cache e do broadcast `contact_deleted`, dispensando o item 3.

**Pronto quando:** `select count(*) from contacts where phone like '%@g.us'` = **0** e `select count(*) from atendimentos a join contacts c on c.id=a.contact_id where c.phone like '%@g.us'` = **0**.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-08-05) — ⚠️ **restart da aplicação pendente com o usuário** (item 3)
- **O que foi feito:** `DELETE FROM contacts WHERE phone LIKE '%@g.us'` → **118 linhas**, e por CASCADE as 2 conversas, 4 mensagens, 120 `contact_inboxes` e 115 `unread_msg_ids`. Mais `DELETE FROM plugin_debug_bus_records WHERE phone LIKE '%@g.us'` → 2 linhas (item 2).
- **Como foi feito / decisões:** via SQL (item 1), não pela API (item 4) — escolha do usuário. Em vez do roteiro manual `SELECT` → `DELETE` → conferir → `COMMIT`, usei **um bloco `DO` dentro da transação com 5 guardas que levantam exceção** (com `ON_ERROR_STOP=1`, qualquer falha vira `ROLLBACK` automático, sem depender de o operador ler o número antes de digitar `COMMIT`): (1) contagem de grupos fora da faixa 1..130 aborta; (2) qualquer `@g.us` com JID fora de `^[0-9]+@g\.us$` aborta; (3) `ROW_COUNT` tem de bater com a contagem prévia; (4) o total de contatos tem de cair **exatamente** o número de grupos (prova que o CASCADE não levou nada a mais); (5) 0 contatos `@g.us` restantes. Predicado por sufixo, nunca por id — como manda o plano.
- **Problemas / pendências:** **o restart da aplicação (item 3) NÃO foi executado** — ficou com o usuário, que faz o redeploy no Coolify. Risco residual reavaliado no código e considerado **fechado sem o restart**: o ramo de roster consulta `contact_repo.get_by_phone` direto no banco ([channel_webhook.py:520](../server/routes/channel_webhook.py#L520)) e só toca no cache `_get_contact` **se** o contato existir — com a linha apagada, o bloco inteiro é pulado; e o caminho de mensagem descarta grupo antes de materializar contato agora que a F1 fechou os 3 canais. O restart segue recomendado por higiene (evicta os `ContactMemory` mortos de `agent_handler._contacts`, que guardam o `contact_id` resolvido uma vez em [memory.py:254](../agent/memory.py#L254)).
- **Verificação:** guardas todas verdes no log da transação — `ANTES -> contatos=15051 | grupos=118 | conversas=2 | mensagens=4 | contact_inboxes=120 | unread=115`; `DELETE -> 118`; `DEPOIS -> contatos=14933`. (O total subiu 15050→15051 entre a F0 e a F2: mais um contato real, não-grupo.)

---

### F3 — Verificação (🔴 sozinha) [depende de: F1, F2]

**Objetivo:** provar que o sintoma sumiu e que nada ficou órfão.

**Itens:**
1. `[paralelo]` Banco — as 4 consultas devem voltar 0: contatos `@g.us`; conversas de contato de grupo; mensagens de contato de grupo; linhas em `contact_inboxes` para grupo.
2. `[paralelo]` Órfãos — conferir que nenhuma tabela de plugin ficou apontando para conversa inexistente (as tabelas de plugin **não têm FK**): `plugin_protocolos_atendimentos`, `plugin_retornos_controle`, `plugin_janela_72h_windows` com `conversation_id` que não existe mais em `atendimentos`. Esperado: 0 (§3.3 já mostrava 0).
3. `[sequencial]` Painel — abrir `https://atendimento.coolify.redesbrasil.com.br/?assignment=unassigned` e confirmar que a aba **"Não atribuídas" mostra 0** (ou o número real de conversas visíveis), sem o rodapé "Mostrando 0 de N".
4. `[sequencial]` Contatos — abrir a tela **Contatos** e confirmar que nenhum `120363…@g.us` aparece na listagem.
5. `[paralelo]` Anotar o total de contatos antes/depois (esperado: **15048 → 14930**).

**Pronto quando:** os 5 itens conferem e o badge da aba bate com a lista renderizada.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída no banco (2026-08-05) — itens 3 e 4 (confirmação visual no painel) pendentes com o usuário
- **O que foi feito:** itens 1, 2 e 5 verificados por consulta. Itens 3 e 4 dependem de sessão no painel — passados ao usuário junto com o restart.
- **Como foi feito / decisões:** o item 1 foi ampliado para 8 consultas (as 4 do plano + `unread_msg_ids`, `debug_bus`, `@newsletter`, `@broadcast`). Ao item 2 acrescentei a verificação do **sintoma-raiz**, que o plano não pedia: contar as conversas que o badge "Não atribuídas" soma (`status='open' AND assignee_user_id IS NULL AND active_agent_key IS NULL`, [conversation_repo.py:565-573](../db/repositories/conversation_repo.py#L565-L573)) **e** procurar qualquer conversa fantasma restante no sistema inteiro (aberta, não atribuída, com 0 mensagens de role visível).
- **Problemas / pendências:** ⚠️ **Órfãos pré-existentes encontrados** nas tabelas de plugin sem FK: `plugin_protocolos_atendimentos` 50 (conv) / 45 (contato), `plugin_protocolos_protocolos` 36, `plugin_protocolos_avaliacoes` 17, `plugin_vendas_ia_conversa` 8, `plugin_agendamento_retorno_items` 4/3, `plugin_janela_72h_windows` 2. **Não foram causados por esta limpeza** — provado: 0 deles referenciam os ids apagados (contatos 15072–15189, conversas 15448/15449); todos apontam para contatos ≤14930 e conversas ≤15183, isto é, faixas anteriores. São herança da migração Chatwoot / limpeza de protocolos de 2026-07-20. Deixados intactos (fora do escopo); merecem plano próprio.
- **Verificação:** item 1 — **todas as 8 consultas = 0**. Item 2 — 0 órfãos atribuíveis a esta operação (query de atribuição por faixa de id). Item 5 — total de contatos **15051 → 14933** (o plano previa 15048 → 14930; o delta de +3 é de contatos reais criados no intervalo). **Sintoma-raiz resolvido: "Não atribuídas" = 0 e nenhuma conversa fantasma restante em todo o banco.** Integridade do resto: 15175 conversas, 653.486 mensagens, 15.600 protocolos, 345 tags, 9 canais, 14 usuários — e por canal, só o `numero_recuperacao` mudou (10 → 8 conversas, exatamente as 2 fantasmas); `Atendimento` 14497, `RedesBrasil_bot` 575, `Equipe_01` 5, todos intactos.

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| `DELETE` em produção | Apagar contato que não era grupo | Predicado por sufixo `@g.us` (nunca por id), `SELECT count(*)` antes do `DELETE` dentro da mesma transação, `COMMIT` só se o número bater |
| Cache do `agent_handler` | SQL direto não dropa `ContactMemory` em memória ⇒ erro de FK num evento de roster tardio | Restart da aplicação na F2 item 3 (ou usar a via de API, que já dropa) |
| Tabelas de plugin sem FK | CASCADE não as alcança; sobraria linha apontando para conversa morta | Medido 0 hoje (§3.3); reconferido na F3 item 2 |
| Recorrência | Uma mensagem de grupo por canal que ainda aceite `group` recria o contato e o ciclo | F1 + P2. Sem elas, esta limpeza tem prazo de validade |
| Ordem F1 × F2 | Se a F1 for pulada e chegar mensagem de grupo entre a F2 e o fim do dia, o inventário renasce | As duas são da mesma wave; rodar as duas antes da F3 |
| Backup | Restaurar um dump de 30+ MB por engano por cima do banco vivo | O dump é **só** rede de segurança; restaurar exige decisão explícita do usuário, nunca do executor |
| WebSocket | A sidebar de quem estiver com o painel aberto pode manter a linha até recarregar | Via SQL não há broadcast `contact_deleted`; instruir recarregar a página (F3 item 3 já força) |

---

## 7. Perguntas em aberto

**P1 — O canal `Equipe_01` precisa mesmo receber grupos?**
⏸️ **AGUARDANDO O USUÁRIO.** Contexto: é o único canal GOWA que ainda lista `group` (§3.4). Se ele participa de grupos de trabalho que a equipe atende pelo painel, desmarcar sumiria com essas conversas.
(a) Desmarcar `group` também nele — fecha a torneira de vez.
(b) Manter — aceita-se que grupos desse número virem contato/conversa, e a limpeza vale só para os 118 atuais.
**Recomendação:** (a), se ninguém atende grupo pelo painel hoje. É reversível por um clique.

**P2 — Consertar o código que abre conversa a partir de um aviso de roster?**
⏸️ **ADIADO — fora do escopo deste plano** (o usuário pediu limpeza). Contexto: §2 #1-2-4. Enquanto não for feito, um "saiu do grupo" continua podendo abrir atendimento **se o contato existir**; e o badge continua contando linha que a lista esconde (§2 #5-6).
(a) Espelhar a regra do reopen: role painel-only não CRIA conversa nova (`create_closed=True` ou não resolver conversa) — [memory.py:352-358](../agent/memory.py#L352-L358).
(b) Levar o gate de visibilidade para o `db/filters`, para contagem e lista compartilharem o mesmo `WHERE`.
**Recomendação:** virar um plano próprio depois desta limpeza; (a) é o conserto barato, (b) é o estrutural (mesma classe do plano 69).

---

## 8. Apêndice — o que o executor toca

**Nenhum arquivo de código.** Este plano é 100% operação:

| Camada | Alvo |
|---|---|
| Banco (produção) | `contacts` (DELETE por sufixo `@g.us`) → CASCADE em `atendimentos`, `messages`, `contact_inboxes`, `contact_tags`, `mentions`, `observations`, `unread_msg_ids`, `usage`; `plugin_debug_bus_records` (opcional) |
| UI | Canais → `Equipe_01` → Editar → desmarcar "Grupo / Comunidade" (F1) |
| Infra | `pg_dump` para `~/whatsbot-backups/` (F0); restart da aplicação no Coolify (F2 item 3) |

Arquivos **lidos** (só para entender, não editar): [message_ingest_service.py:350-374](../app/services/message_ingest_service.py#L350-L374), [channel_webhook.py:510-540](../server/routes/channel_webhook.py#L510-L540), [memory.py:328-373](../agent/memory.py#L328-L373), [contact_repo.py:69-72](../db/repositories/contact_repo.py#L69-L72), [contacts.py:894-913](../server/routes/contacts.py#L894-L913).

---

## 9. Checklist de verificação

- [x] Dump em `~/whatsbot-backups/` existe e é listável (`pg_restore -l`) — `whatsbot-pre-plano102-20260805-1742.dump`, 35 MB, 825 TOC
- [x] Números de §3 reconferidos imediatamente antes do `DELETE` (podem ter crescido) — 118 grupos, total 15051
- [x] `DELETE` rodou dentro de transação, com contagem conferida antes do `COMMIT` — 5 guardas com `RAISE EXCEPTION` + `ON_ERROR_STOP=1`
- [x] `select count(*) from contacts where phone like '%@g.us'` = 0
- [x] Nenhuma conversa/mensagem de contato de grupo sobrou
- [x] Nenhuma tabela de plugin ficou com `conversation_id` órfão **por causa desta limpeza** (órfãos pré-existentes de 2026-07 continuam lá — ver F3)
- [ ] ⏳ **Aplicação reiniciada** (cache de `ContactMemory` limpo) — **pendente com o usuário** (higiene; risco de FK já fechado por F1+F2, ver F2)
- [ ] ⏳ Painel recarregado: aba "Não atribuídas" bate com a lista, sem "Mostrando 0 de N" — **pendente** (no banco a contagem já é 0)
- [ ] ⏳ Tela Contatos sem nenhum `120363…@g.us` — **pendente** (no banco já são 0)
- [x] `Equipe_01` com `group` desmarcado — P1 decidida por (a); os 3 canais GOWA em `["person","person_lid"]`
- [x] Total de contatos: **15051 → 14933** (plano previa 15048 → 14930; +3 contatos reais criados no intervalo)
