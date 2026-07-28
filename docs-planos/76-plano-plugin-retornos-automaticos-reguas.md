# Plano 76 — Plugin `retornos`: follow-up automático por régua de regras (porte do nexus-retorno)

> **Status:** PLANEJAMENTO · **Data:** 2026-07-23 · **Escopo:** grande (1 plugin, 6 workstreams: motor de regras · dispatcher · ações/IA · eventos/lifecycle · UI builder · UI monitor)
> **Origem:** pedido do usuário — replicar no WhatsBot o módulo **Retornos** do Nexus (`/opt/nexus/nexus-retorno`), que faz follow-up automático de conversas do Chatwoot com um **construtor visual de regras aninhadas** (grupos E/OU), sequência de retornos, mensagens/notas/mídia e ativação de IA por nota privada `@Bia`. **Método:** leitura do código real dos dois sistemas (`arquivo:linha` verificado), consulta ao banco de produção do Nexus (`RBNexusDB` via vault — 6.529 disparos reais, 2.586 controles) e workflow de 10 sub-agentes de investigação. A parte visual deve ficar **parecida com a do Nexus** para outras pessoas configurarem/editarem réguas.
> Diferença fundamental já resolvida: no Chatwoot a IA era um agente **externo** que escutava a nota `@Bia`; no WhatsBot **IA e atendimento são o mesmo processo** (motor AGNO), então o passo "aciona IA" chama o AGNO **direto** (`agent_handler.aprocess_message`, caminho `_run_private_ai` já existente).
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 1. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| **D1** ✅ 2026-07-23 | **Manter `agendamento_retorno`; substituir só `retorno_automatico`.** O novo plugin absorve a função de **régua automática** e convive com o agendamento **manual** pelo atendente (botão dentro da conversa). | Novo plugin **id `retornos`**. Na instalação, `retorno_automatico` é **desativado** (F11) para não gerar nota duplicada. `agendamento_retorno` fica intacto. |
| **D2** ✅ 2026-07-23 | Quando um passo aciona a IA, o comportamento é **"IA responde AGORA com a instrução do passo"** (mensagem proativa gerada pelo AGNO e **enviada ao cliente**). É o equivalente honesto do `@Bia`. | Tipo de mensagem de passo `ia_responde_agora`: a `instrução` vira o "turno do usuário sintético" passado a `aprocess_message`; a resposta é enviada. **Não** há "religar IA" nem "IA via mensagem fixa" no MVP. |
| **D3** ✅ 2026-07-23 | Fora da **janela de 24h** da Meta → **só nota privada** (comportamento literal do Nexus). **Sem templates HSM no MVP.** | Nenhuma integração com `template_service`. Quando `outbound_router.session_open(...) == False`, o dispatcher **substitui** a ação do passo por uma `private_note` de aviso ao atendente. |
| **D4** ✅ 2026-07-23 | Construtor visual com **aninhamento completo, profundidade arbitrária** (grupo dentro de grupo). | Avaliador **recursivo** em Python **e** em JS; UI **recursiva** (`RegrasList → CondicaoRow \| GrupoBlock`). Paridade total com a régua real "Principal - API Meta" (que tem grupo-dentro-de-grupo). |
| **P** (princípio) | Padrão do repo: **policy no plugin, mechanism no core** — nenhum `if provider ==`, nenhuma aba nova no painel de Configurações do core, nenhuma tabela do core alterada. | Tudo entra por `spawn_task`, bus de eventos, `outbound_router`, `conversation_service`, `agent_handler` e RBAC de plugin. **Zero** migration/coluna no core. |
| **P** (princípio) | **Não portar os bugs conhecidos do Nexus** (nada em produção no WhatsBot ⇒ implementação correta desde o dia 1). | Ver §6 "Bugs do Nexus que NÃO serão portados" — cada um vira uma regra de correção travada (D5–D8). |
| **D5** ✅ 2026-07-23 | Passo com filtro **FALSO** → **reagenda o MESMO passo** (retry com contador + deadline); ao esgotar, **cancela** e sinaliza no monitor. **Nunca** avança fingindo progresso. | Corrige o "Defeito B" do Nexus (skip que avança sem contar mata a cadeia em silêncio — `INVESTIGACAO-RETORNOS-NAO-ENVIADOS.md`). |
| **D6** ✅ 2026-07-23 | Disparo só **CONTA** (`disparos_enviados += 1`) quando **≥1 mensagem saiu com sucesso**. | Corrige o incremento indevido do Nexus (`dispatched=true` incondicional, `dispatcher.service.ts:374`). |
| **D7** ✅ 2026-07-23 | Operador `between` em campo **hora** implementa **wrap-around de meia-noite** desde o dia 1 (`min>max` ⇒ `v>=min OR v<=max`). | Corrige o `TODO-wrap-around-between.md` (aberto e não resolvido no Nexus) e elimina o workaround "grupo OU por faixa" que confundia o agendador. |
| **D8** ✅ 2026-07-23 | Campo `on_reply` (`reset`\|`cancel`) é **realmente lido** e há gatilhos de cancelamento configuráveis por régua. | Corrige o Nexus, onde `onReply` existe no schema/UI mas **nunca é lido** (comportamento sempre `reset`, hardcoded). |
| **D9** ✅ 2026-07-23 | Fuso horário = **offset fixo configurável** por régua (`tz_offset_hours`, default −3), sem depender do TZ do processo. | Copia a decisão de `retorno_automatico/schedule.py`; evita o bug do Nexus (container UTC desloca `getHours()` em 3h em silêncio). |

---

## 2. Resumo executivo

O Nexus-Retorno é um NestJS que, a cada minuto, varre um controle-por-conversa, avalia uma **árvore de regras** (grupos E/OU + condições sobre campos do Chatwoot/virtuais/do módulo) e dispara a próxima mensagem de uma sequência — ou pula se as regras não batem. Em produção ele roda **exclusivamente** com um único tipo de mensagem: uma **nota privada `@Bia …`** que um bot externo do Chatwoot lê para acionar a IA.

O porte para o WhatsBot vira o **plugin `retornos`**, 100% na borda (nenhum patch no core): tabelas próprias (`plugin_retornos_*`), um **motor de regras puro** (recursivo, testado), um **dispatcher** por minuto via `ctx.spawn_task` (padrão idêntico ao `agendamento_retorno`/`retorno_automatico`), **ações** que reusam `outbound_router`/`agent_handler`/`conversation_service`, e **duas telas Preact/HTM** (editor de réguas com construtor de regras aninhado + monitor). Como a IA é **interna**, o passo `ia_responde_agora` substitui o `@Bia`: chama `aprocess_message` e envia a resposta ao cliente. Fora da janela de 24h da Meta, o passo vira **nota privada** (D3). Os **4 bugs conhecidos do Nexus não são portados** (D5–D8).

Achado que encurta o trabalho: **já existem 2 plugins instalados** que são o protótipo disso — `retorno_automatico` (verificador por minuto, nota privada quando o cliente fica em silêncio) e `agendamento_retorno` (follow-up agendado, dispara nota/mensagem real, reabre+atribui a conversa). O novo plugin **copia a espinha dorsal** de ambos e acrescenta o que falta: a **régua de regras aninhadas** e a **UI de configuração**.

---

## 3. Como funciona hoje (mapa)

### 3.1 Nexus-Retorno — os 2 motores (verificado)

| Motor | Arquivo:linha | O que faz |
|---|---|---|
| **Webhook** (cria/reseta controle) | `nexus-retorno/server/modules/chatwoot-webhook/chatwoot-webhook.service.ts` | Só `message_created` + `message_type='incoming'` + `private!==true` (mensagem do **cliente**). Se controle `active`: **reset** mantendo config. Se novo/`completed`/`cancelled`: **roteia** pela 1ª config `active` (por `posicao`) cujo **R1** passa em `avaliarGate` (poda `virtual.*`/`modulo.*`). |
| **Cron dispatcher** (dispara) | `nexus-retorno/server/modules/dispatcher/dispatcher.service.ts` | `@Cron(EVERY_MINUTE)`: (1) recovery de lock `processing>5min`; (2) `UPDATE … SET processing=true … WHERE next_retorno_at<=NOW() AND status='active' AND processing=false RETURNING *`; (3) grace window (`DISPATCH_GRACE_MINUTES`, default 15 → **cancela** atrasado); (4) avalia filtros do passo → dispara ou **pula avançando** (bug D5). |

**Motor de regras** (`nexus-retorno/server/modules/retornos/filtros-engine.service.ts`):
- `avaliarRegras` — combinação **esquerda→direita**: `acc = conector==='OU' ? acc||r : acc&&r` (sem precedência; grupos = parênteses). Primeira regra ignora `conector`.
- `avaliarRegra` — `grupo` recursa em `avaliarRegras(regra.regras)`; `condicao` → `avaliarCondicao`.
- `aplicarOperador` — 17 operadores (§5.2). `between` em hora **NÃO** cruza meia-noite (`a>=min && a<=max`) → bug D7.
- `calcularProximoAgendamento` — só campos `isTemporal:true` geram `nextRetornoAt`; senão `NOW()+1min`. É um **hint** (o cron reavalia tudo na hora).

**Estrutura de dados da árvore** (`filtros.types.ts`, confirmada no banco de produção):
```jsonc
{ "regras": [
  { "tipo": "grupo", "conector": "OU", "regras": [
    { "tipo": "condicao", "campo": "modulo.n_disparos_enviados", "operador": "eq", "valor": 0 },
    { "tipo": "grupo", "conector": "E", "regras": [ /* grupo DENTRO de grupo — D4 */ ] }
  ]}
]}
```

### 3.2 Estado real de produção do Nexus (medido no banco `RBNexusDB`)

| Métrica | Valor | Fonte |
|---|---|---|
| Disparos totais (`logs.event='retorno.dispatched'`) | **6.529** | tabela `logs` |
| Skips (`retorno.skipped`) | **4.060** | idem — evidência do bug D5 em escala |
| Controles `completed` / `cancelled` | 1.244 / 1.342 | `retornos_controle` |
| Tipo de mensagem usado em produção | **100% `private_note` `@Bia …`** | `retornos_mensagens` |
| `is_meta_api` nas réguas ativas principais | **true** (janela 24h ativa) | `retornos_configuracoes` |
| Réguas com grupo-dentro-de-grupo | **Sim** (régua "Principal - API Meta") | confirma D4 |

⚠️ **Gotcha**: a régua real usa `between 16:00..07:30` **quebrada num grupo OU** (`>=16:00 OU <=07:30`) exatamente porque o `between` do Nexus não cruza meia-noite. D7 elimina essa necessidade.

### 3.3 WhatsBot — as costuras que o plugin vai reusar (verificado)

| Precisa de… | Core expõe | Arquivo:linha |
|---|---|---|
| Tarefa periódica (cron) owned pelo plugin | `ctx.spawn_task(name, coro_factory, policy=PERMANENT)` | `plugins/context.py:316` · `runtime/supervisor.py:27` |
| Enviar texto/mídia por canal | `outbound_router.send_text/send_media` | `channels/outbound.py:80` · `:91` |
| Saber se a janela 24h está aberta | `outbound_router.session_open(channel_id, last_inbound_ts)` | `channels/outbound.py:48` |
| Capability do canal (ex.: `templates`) | `outbound_router.supports(channel_id, cap)` | `channels/outbound.py:45` |
| Hora da última msg do cliente | `message_repo.last_inbound_ts(conversation_id=…)` | `db/repositories/message_repo.py:267` |
| IA responde agora (proativa) | `agent_handler.aprocess_message(phone, text, save_user_message=False, save_response=False, channel_id=…)` | `server/routes/contacts.py:1160` (`_run_private_ai`) |
| Reabrir/atribuir/desligar IA (coroutines) via thread | `_run_on_loop(coro)` = `run_coroutine_threadsafe` | `storages/plugins/agendamento_retorno/logic.py:44` |
| `set_status` / `assign` / `archive` / `set_ai` / `_clear_transfer_tag` | `conversation_service.*` | `app/services/conversation_service.py:177,402,245,523,332` |
| Gravar nota privada + broadcast | `ContactMemory.add_message('private_note', …)` (reopen=False) | `agent/memory.py:389` (via `agendamento_retorno/logic.py:389`) |
| Salvar msg de saída do operador | `agent_handler.save_operator_message(…)` | `storages/plugins/agendamento_retorno/logic.py:416` |
| Runtime de canais (registry/outbound/ingest) | `get_channel_runtime()` | `storages/plugins/agendamento_retorno/logic.py:410` |
| Candidatos (conversas IA sem humano) | SELECT `atendimentos` + `contacts` (`ai_active=1 AND active_agent_key IS NOT NULL AND assignee_user_id IS NULL`) | `storages/plugins/retorno_automatico/logic.py:83` (`list_candidates`) |
| Expediente (offset fixo, `is_open_now`) | função pura | `storages/plugins/retorno_automatico/schedule.py` |
| Reagir a mensagem já salva | evento de bus `message.saved` | CLAUDE.md §"Events e Filters" |
| Cancelar em resolver/atribuir/desligar-IA | eventos `conversation.*` / `contact.ai_toggled` | idem (nomes a confirmar em F5) |

### 3.4 Construtor de regras — o que o WhatsBot **já tem** e o que **falta** (verificado)

| Camada | Existe hoje | Cobre aninhamento E/OU? | Reuso |
|---|---|---|---|
| Backend `db/filters/` (`registry.py`, `spec.py`, `translate.py`) | Vocabulário **plano** de dimensões+operadores; `FilterSpec.match` é um único `and`/`or` global | ❌ Não | **Vocabulário-base** (nomes de dimensão/operador) como referência; **não** o avaliador |
| Frontend `ConversationFilterDialog.js` / `ContactFilterDialog.js` | Lista **plana** de cláusulas `{dim,op,value}`, sempre `AND` (`matchesAdvFilters=every`) | ❌ Não | **ValorInput por tipo** (`OptionListSelect`, multi-select, etc.) — reaproveitar os inputs, não a estrutura |
| Avaliador client-side `conversationRows.clauseMatches` | Avalia cláusula plana sobre rows já carregadas | ❌ Não | Referência de como avaliar no cliente |

⚠️ **Conclusão**: o aninhamento E/OU **não existe em nenhuma camada** do WhatsBot. O motor recursivo (Python + JS) e a UI recursiva são **construção nova** — a parte mais cara do porte (D4). Os **inputs de valor** e o **vocabulário de campos** são reaproveitáveis.

---

## 4. Inventário de módulos do plugin

Todos os caminhos abaixo são **novos**, sob `assets/plugin_examples/retornos/` (fonte versionada — ver P1) e instalados em `storages/plugins/retornos/`.

| Módulo | Papel | Espelha | Risco | Esforço |
|---|---|---|---|---|
| `plugin.yaml` | manifest: `entry.{routes,lifecycle,events,settings}`, `migrations`, `screens[]` (2 telas), `rbac`, `frontend_extends` (opcional) | `agendamento_retorno/plugin.yaml` | baixo | S |
| `migrations/001_initial.sql` | 5 tabelas `plugin_retornos_*` (prefixo obrigatório) | `agendamento_retorno/migrations` | médio | M |
| `rules.py` | **PURO** (sem DB): `avaliar(arvore, ctx)` recursivo + `proximo_instante(arvore, ctx)` + operadores (com `between` wrap-around D7) | `filtros-engine.service.ts` | **alto** | L |
| `catalog.py` | catálogo de **campos** (id/label/grupo/tipo/operadores/fonte) + labels de operador | `filtros-metadata.ts` | baixo | M |
| `schedule.py` | expediente/offset fixo (`is_open_now`, `parse_hhmm`) — **cópia** | `retorno_automatico/schedule.py` | baixo | S |
| `evalctx.py` | monta o `ctx` de avaliação **sem rede** (repos do core) | `resolverCampo` do engine | médio | M |
| `repo.py` | data-access das 5 tabelas (SQLAlchemy Core, `get_engine`) | `configuracoes/retornos/controle.service.ts` | médio | M |
| `dispatcher.py` | ciclo por minuto: recovery → lock atômico → grace → avaliar → dispara **ou reagenda o mesmo passo** (D5) → conta só no sucesso (D6) → agenda próximo | `dispatcher.service.ts` | **alto** | L |
| `actions.py` | executores por tipo de mensagem; gate `session_open` → nota privada (D3) | `dispatcher.sendMessage/sendMediaMessage/sendPrivateNote` | alto | M |
| `events.py` | `message.saved` (criar/reset/cancel por `on_reply`); `conversation.*`/`contact.ai_toggled` (cancelar); `app.startup` (recovery de locks órfãos) | `chatwoot-webhook.service.ts` | médio | M |
| `lifecycle.py` | `setup(ctx)`: captura loop + `spawn_task('scheduler', …)` | `retorno_automatico/lifecycle.py` | baixo | S |
| `settings.py` | settings declarativas globais (delay entre msgs, grace, expediente default) | `retorno_automatico/settings.py` | baixo | S |
| `routes.py` | CRUD réguas/passos/mensagens + reorder + duplicate + export/import + monitor + `/metadata` | `configuracoes/retornos/controle/chatwoot-metadata` controllers | médio | L |
| `static/rules.js` | **PURO**: espelho JS de `rules.py` (avaliação + contagem para preview) + `node --test` | — (irmão obrigatório) | alto | M |
| `static/RegraBuilder.js` | construtor **recursivo** `RegrasList → CondicaoRow \| GrupoBlock`; `ValorInput` por tipo (reusa `OptionListSelect`) | `FiltrosBuilder.tsx`/`CondicaoRow.tsx`/`ValorInput.tsx` | **alto** | L |
| `static/reguas.js` (screen `config:false`) | lista de réguas + editor (accordion de passos, DnD HTML5 nativo, editor de mensagens, export/import) | `configuracoes/[slug]/page.tsx` | alto | L |
| `static/monitor.js` (screen `config:false`) | controles em andamento, próximo disparo, reset/cancelar, WS ao vivo | `monitor/page.tsx` | médio | M |
| `static/extends.js` (opcional) | botão "Retornos" no header da conversa (ver/parar régua da conversa) | `agendamento_retorno/static/extends.js` | baixo | S |

### 4.1 Falsos positivos descartados

| Item | Por que parece necessário | Por que NÃO é |
|---|---|---|
| Reusar `db/filters/` como avaliador | Já é um "motor de filtros" | É **plano** (um único `match` global) — não avalia árvore E/OU. Só o **vocabulário** serve. |
| Integrar `template_service` para janela 24h | O Nexus é `is_meta_api` | **D3**: fora da janela = só nota privada. Sem templates no MVP. |
| Coluna nova no core p/ "está na janela 24h?" | Precisa saber se pode mandar texto livre | Já é **derivado** por `outbound_router.session_open(channel_id, last_inbound_ts)` — zero mudança no core (relatório `wb-24h-templates`). |
| Migrar dados do `retorno_automatico` | Vai ser desativado (D1) | Ele é **stateless** (sem tabela — as próprias notas são a memória). Nada a migrar; basta desativar. |
| Proxy de metadados estilo Chatwoot (cache 5min) | O Nexus tem `/chatwoot-metadata` | Inboxes/agentes/etiquetas/canais são **locais** (repos do core). `/metadata` do plugin lê direto, sem rede externa. |
| Webhook público de entrada | O Nexus recebe webhook do Chatwoot | No WhatsBot a entrada é o **evento de bus** `message.saved` (in-process). Sem endpoint público. |
| `chatwoot.time_id` (Time) | Campo do Nexus | WhatsBot **a confirmar** se tem "times"/teams no schema — provavelmente **omitir** no catálogo do MVP. |

---

## 5. Especificação do motor de regras (a peça crítica — D4)

### 5.1 Estrutura da árvore (idêntica ao Nexus, para import/export compatível)

```jsonc
Arvore  = { "regras": Regra[] }
Regra   = Condicao | Grupo
Condicao= { "tipo":"condicao", "conector"?:"E"|"OU", "campo":string, "operador":string, "valor":unknown }
Grupo   = { "tipo":"grupo",    "conector"?:"E"|"OU", "regras":Regra[] }
```
- Avaliação **esquerda→direita** dentro de cada lista; a **1ª** regra ignora `conector`.
- `Grupo` é avaliado recursivamente e vira **um** booleano (parênteses). **Profundidade arbitrária** (D4).
- Árvore vazia / `regras:[]` ⇒ **true** (sempre passa) — igual ao Nexus.

### 5.2 Operadores (porte de `aplicarOperador`, com correções)

| Operador | Semântica | Tipos | Nota de porte |
|---|---|---|---|
| `eq`/`neq` | igualdade normalizada (número vs string) | todos | `number`→float; resto→str |
| `exists`/`not_exists` | valor presente / vazio | select/agente | |
| `contains`/`not_contains`/`starts_with`/`ends_with` | substring case-insensitive | text | |
| `gt`/`gte`/`lt`/`lte` | comparação | number/date/time | `time`→minutos do dia; `date`→epoch |
| `between` | `min ≤ v ≤ max` | number/date/**time** | **time com wrap-around (D7)**: se `min>max` ⇒ `v≥min OR v≤max` |
| `in`/`not_in` | pertence a lista | select/enum | |
| `has_any`/`has_all` | interseção com lista (etiquetas) | multi-select | |

### 5.3 Catálogo de campos (Nexus → WhatsBot)

`grupo` mantém os rótulos do Nexus para paridade visual: **Chatwoot** (renomear rótulo para **"Atendimento"** na UI), **Virtual**, **Módulo**.

| Campo (id) | Rótulo UI | Grupo | Tipo | Origem no WhatsBot (`arquivo`) |
|---|---|---|---|---|
| `chatwoot.caixa_id` | Caixa de Entrada | Atendimento | select | `atendimentos.inbox_id` |
| `chatwoot.agente_id` | Atendente atribuído | Atendimento | select | `atendimentos.assignee_user_id` (humano) |
| `chatwoot.status` | Status | Atendimento | enum | `atendimentos.status` — **só `open`/`closed`** (mapear Aberta/Fechada) |
| `chatwoot.etiquetas` | Etiquetas da conversa | Atendimento | multi-select | labels de conversa (`conv_labels`) |
| `contato.tags` | Etiquetas do contato | Atendimento | multi-select | `contact_tags` (dimensão nova, WhatsBot) |
| `chatwoot.prioridade` | Prioridade | Atendimento | enum | `atendimentos.priority` (**confirmar coluna** em F1) |
| `chatwoot.criado_em` | Criado em | Atendimento | date | `atendimentos.created_at` |
| `chatwoot.ultima_atividade` | Última atividade | Atendimento | date | última atividade da conversa |
| `wb.canal` | Canal | Atendimento | select | provider/canal (`db/filters` dim `channel`) — WhatsBot |
| `wb.contact_type` | Tipo de contato | Atendimento | enum | `contacts.contact_type` — WhatsBot |
| `wb.agente_ia` | Agente IA ativo | Atendimento | select | `atendimentos.active_agent_key` — WhatsBot |
| `wb.ia_ativa` | IA ativa | Atendimento | enum(sim/não) | `atendimentos.ai_active` — WhatsBot |
| `virtual.hora_atual` | Hora atual | Virtual | time | relógio (offset fixo D9) — `isTemporal` |
| `virtual.data_atual` | Data atual | Virtual | date | relógio — `isTemporal` |
| `virtual.dia_semana` | Dia da semana | Virtual | enum(0..6) | relógio |
| `modulo.n_disparos_enviados` | Nº de disparos enviados | Módulo | number | `plugin_retornos_controle.disparos_enviados` |
| `modulo.hora_ultimo_contato` | Hora da última msg do cliente | Módulo | time | `message_repo.last_inbound_ts` → HH:MM na tz da régua |
| `modulo.horas_desde_ultimo_contato` | Horas desde a última msg | Módulo | number | derivado — `isTemporal` |
| `modulo.dias_desde_ultimo_contato` | Dias desde a última msg | Módulo | number | derivado — `isTemporal` |
| `modulo.dias_calendario_desde_contato` | Dias de calendário desde a última msg | Módulo | number | derivado — `isTemporal` |

> **Omitidos do MVP:** `chatwoot.time_id` (Times — a confirmar se o WhatsBot tem o conceito) e `modulo.disparo_anterior_id` (já era legado oculto no Nexus). Import de árvore que os referencie: condição de campo desconhecido ⇒ **false** (igual ao Nexus), sem quebrar.

### 5.4 `proximo_instante` (hint de agendamento)

Porte de `calcularProximoAgendamento`: para cada condição `isTemporal`, calcula o próximo timestamp candidato; usa o **mais cedo ≥ agora**; sem candidato ⇒ `agora+60s` (o cron reavalia). É só um **hint** — o dispatcher sempre reavalia a árvore inteira na hora (D5).

---

## 6. Bugs do Nexus que NÃO serão portados (travados em D5–D8)

| # | Bug no Nexus (`arquivo:linha`) | Sintoma em produção | Correção no plugin |
|---|---|---|---|
| B (D5) | Filtro falso → `avancarParaProximo` incrementa `currentStep` sem enviar (`dispatcher.service.ts:377-423`) | 4.060 `retorno.skipped`; passos que gateiam por `n_disparos` nunca disparam → cadeia morre em silêncio | Filtro falso → **reagenda o MESMO passo** (`tentativas_passo+1`, `next_at` recalculado); esgotou `max_tentativas`/`deadline` → `status='expired'` + log |
| — (D6) | `messagesSentCount` sobe mesmo com todas as mensagens falhando (`dispatched=true` incondicional, `:374`) | contador infla; réguas por `n_disparos` disparam cedo | Conta **só** se ≥1 mensagem saiu com sucesso |
| — (D7) | `between` em hora não cruza meia-noite (`TODO-wrap-around-between.md`) | réguas precisam do workaround "grupo OU" que confunde o agendador | wrap-around nativo (§5.2) |
| — (D8) | `onReply` (`reset`/`cancel`) nunca é lido (hardcoded reset) | UI mente: escolher "cancel" não faz nada | `on_reply` lido em `events.py`; + gatilhos de cancelamento configuráveis |

---

## 7. Modelo de dados (migration `001_initial.sql`, prefixo `plugin_retornos_` obrigatório)

| Tabela | Colunas-chave | Papel |
|---|---|---|
| `plugin_retornos_reguas` | `id` PK, `nome`, `descricao`, `posicao` (prioridade), `ativo`, `filtros_entrada` JSONB (**gate explícito de entrada** — não o 1º passo, corrige a confusão de roteamento do Nexus), `on_reply` (`reset`\|`cancel`), `cancel_on_resolve` bool, `cancel_on_assign_human` bool, `tz_offset_hours`, `business_*` (expediente), `created_at`/`updated_at` | Régua (equivale à `RetornosConfiguracao`) |
| `plugin_retornos_passos` | `id` PK, `regua_id` FK, `ordem`, `nome`, `filtros` JSONB (árvore recursiva), `proxima_mensagem_index` (cursor A/B), `max_tentativas` (D5), `deadline_min` (D5) | Passo da sequência (`RetornosRetorno`) |
| `plugin_retornos_mensagens` | `id` PK, `passo_id` FK, `ordem`, `tipo` (`text`\|`private_note`\|`image`\|`audio`\|`video`\|`document`\|`ia_responde_agora`), `content` (texto **ou** instrução da IA), `media_path`/`media_url`/`file_name`, `testando` bool (A/B) | Mensagem a enviar (`RetornosMensagem`) |
| `plugin_retornos_controle` | `conversation_id` **UNIQUE**, `regua_id`, `contact_id`, `phone`, `channel_id`, `inbox_id`, `passo_atual_id`, `disparos_enviados`, `tentativas_passo`, `last_client_ts`, `next_at`, `processing` bool, `processing_since`, `status` (`active`\|`completed`\|`cancelled`\|`expired`), `last_error`, timestamps. Índices: `(status, next_at)`, `(processing)` | Rastreio 1:1 por conversa (`RetornosControle`) |
| `plugin_retornos_log` | `id`, `evento` (`dispatched`\|`skipped`\|`retry`\|`expired`\|`cancelled`\|`failed`\|`reset`), `regua_id`, `controle_id`, `conversation_id`, `nivel`, `data` JSONB, `ts` | Observabilidade (substitui os `webhooks` de saída do Nexus) |

Regras do repo: `CREATE TABLE IF NOT EXISTS`, sem `;` em comentário (o migrator splita por `;`), toda tabela/índice com prefixo `plugin_retornos_`.

---

## 8. Fases / Roadmap

### 8.1 Waves e dependências

```
WAVE 0  F0(skeleton+migrations) ─┐   F1(rules.py+catalog)  ·  F2(rules.js)      ← F1/F2 puros, paralelos entre si
                                 │        (F1/F2 independem de F0)
                                 │  (barreira: F0 cria tabelas → libera F3)
WAVE 1  F3(repo+evalctx) ────────┘   [depende: F0, F1]
WAVE 2  F4(dispatcher) · F5(actions) · F6(events+lifecycle)   [depende: F3, F1]   ← 3 paralelos
           │ (barreira: F4/F5/F6 = backend vivo → libera UI e testes)
WAVE 3  F7(routes) ──→ F8(RegraBuilder) · F9(reguas screen) · F10(monitor screen)  [depende: F7, F2]
WAVE 4  F11(migração retorno_automatico + seed) · F12(testes e2e + zip + docs)     [depende: tudo]
```

### 8.2 Tabela de fases

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | Skeleton + migrations | 🔴 (bloqueia F3) | médio | plugin aparece em `/plugins`, migrations aplicam, tabelas existem |
| 0 | **F1** | `rules.py` + `catalog.py` + `schedule.py` | 🟢 [bloqueia F3,F4] | **alto** | `pytest` do motor verde (casos do §5 + wrap-around + a régua real de produção) |
| 0 | **F2** | `static/rules.js` (espelho) | 🟢 | alto | `node --test` verde; paridade com `rules.py` nos mesmos vetores |
| 1 | **F3** | `repo.py` + `evalctx.py` | 🔴 [depende F0,F1] | médio | CRUD round-trip no banco de teste; `evalctx` monta ctx real de uma conversa sem rede |
| 2 | **F4** | `dispatcher.py` | 🟢 [depende F3,F1] | **alto** | ciclo dispara/reagenda/expira corretamente contra controles semeados (teste com `next_at=now`) |
| 2 | **F5** | `actions.py` | 🟢 [depende F3] | alto | cada tipo de msg envia; `session_open=False` ⇒ nota privada (D3); `ia_responde_agora` chama AGNO e envia |
| 2 | **F6** | `events.py` + `lifecycle.py` + `settings.py` | 🟢 [depende F3,F1] | médio | msg do cliente cria/reseta/cancela controle por `filtros_entrada`+`on_reply`; `scheduler` aparece no Runtime |
| 3 | **F7** | `routes.py` + `/metadata` | 🔴 [depende F3] | médio | todos os endpoints respondem `{ok,data}`; `/metadata` lista campos/operadores/inboxes/agentes/etiquetas/canais |
| 3 | **F8** | `RegraBuilder.js` (recursivo) | 🟢 [depende F2,F7] | **alto** | cria/edita árvore com grupo-dentro-de-grupo; E/OU alterna; preview via `rules.js` bate com backend |
| 3 | **F9** | `reguas.js` (screen) | 🟢 [depende F7,F8] | alto | criar régua, adicionar passos (accordion+DnD), editar mensagens, export/import JSON; legível no dark |
| 3 | **F10** | `monitor.js` (screen) | 🟢 [depende F7] | médio | lista controles, próximo disparo, reset/cancelar, atualização por WS |
| 4 | **F11** | Desativar `retorno_automatico` + seed opcional de régua-espelho | 🔴 [depende F4-F6] | médio | na instalação, `retorno_automatico` fica `enabled=0`; nenhuma nota duplicada |
| 4 | **F12** | Testes e2e + gerar `.zip` + docs | 🔴 [depende tudo] | médio | suíte verde no Postgres; `.zip` importável; seção no CLAUDE.md/README do plugin |

---

### Fase F0 — Skeleton + migrations
**Objetivo:** o plugin `retornos` existe, carrega e cria suas 5 tabelas.
- **Itens:**
  - `[sequencial]` Criar `assets/plugin_examples/retornos/` com `plugin.yaml` (id `retornos`, `entry`, `migrations`, `screens[]` com 2 telas `config:false`, `rbac` grupo "Retornos" com `view`/`edit`/`monitor`), `__init__.py`. Base: `agendamento_retorno/plugin.yaml`.
  - `[sequencial]` `migrations/001_initial.sql` — as 5 tabelas do §7 (prefixo, `IF NOT EXISTS`, sem `;` em comentário).
  - `[paralelo]` `settings.py` (delay entre msgs, grace default, expediente default) — pode ser stub inicial.
- **Pronto quando:** copiar a pasta para `storages/plugins/retornos/`, reiniciar, o card aparece em `/plugins` sem `load_error`, e `\dt plugin_retornos_*` no Postgres mostra as 5 tabelas.

#### Status de execução — Fase F0
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase F1 — Motor de regras Python (`rules.py` + `catalog.py` + `schedule.py`)
**Objetivo:** avaliador recursivo puro + catálogo de campos + expediente, testados.
- **Itens:**
  - `[paralelo]` `catalog.py` — porte de `filtros-metadata.ts` (§5.3), incluindo os campos WhatsBot novos; **confirmar** a coluna `atendimentos.priority` (grep em `db/tables.py`); decidir omissão de `time_id`.
  - `[sequencial]` `rules.py` — `avaliar(arvore, ctx)` recursivo (esquerda→direita, grupos como parênteses), `aplicar_operador` (17 operadores, **`between` com wrap-around** D7), `proximo_instante(arvore, ctx)`.
  - `[paralelo]` `schedule.py` — **cópia** de `retorno_automatico/schedule.py` (`is_open_now`, `parse_hhmm`, offset fixo D9).
  - `[sequencial]` `tests/test_retornos_rules.py` — vetores do §5 + os 2 filtros da régua real de produção (§3.1/§3.2) devem casar como no Nexus, e o `between 16:00..07:30` (wrap) deve funcionar **sem** o workaround de grupo OU.
- **Pronto quando:** `venv/bin/python -m pytest tests/test_retornos_rules.py -q` verde.

#### Status de execução — Fase F1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase F2 — Espelho JS do motor (`static/rules.js`)
**Objetivo:** avaliação/contagem no cliente para preview da UI, idêntica ao Python.
- **Itens:**
  - `[sequencial]` `static/rules.js` — porte 1:1 de `rules.py` (mesma árvore, mesmos operadores, mesmo wrap-around). É **irmão obrigatório** do backend (usado no preview do builder).
  - `[sequencial]` `static/rules.test.js` (`node --test`) — os **mesmos vetores** de F1.
  - ⚠️ **Armadilha HTM** (memória [[htm-template-crase-quebra-modulo]]): crase/`${}` dentro de comentário em `html\`...\`` fecha o template. `rules.js` é lógica pura (sem HTM), então seguro — mas os componentes de F8/F9 não.
- **Pronto quando:** `node --test static/rules.test.js` verde e batendo com o Python nos mesmos casos.

#### Status de execução — Fase F2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_ · **Como foi feito:** _(preencher)_ · **Problemas:** _(preencher)_ · **Verificação:** _(preencher)_

---

### Fase F3 — Data-access + contexto de avaliação (`repo.py` + `evalctx.py`)
**Objetivo:** ler/gravar as tabelas e montar o `ctx` real de uma conversa sem rede.
- **Itens:**
  - `[paralelo]` `repo.py` — CRUD de réguas/passos/mensagens/controle/log via `get_engine()` (padrão do CLAUDE.md); lock atômico `UPDATE … RETURNING` para o dispatcher.
  - `[paralelo]` `evalctx.py` — resolve cada campo do §5.3 a partir de `conversation_repo`/`message_repo.last_inbound_ts`/labels/tags/`ai_settings`, **sem** chamada de rede; aplica a tz da régua (D9).
- **Pronto quando:** teste de round-trip (criar régua+passos+mensagens, ler de volta) verde no banco de teste; `evalctx` monta o dict de uma conversa real e `rules.avaliar` roda sobre ele.

#### Status de execução — Fase F3
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_ · **Como foi feito:** _(preencher)_ · **Problemas:** _(preencher)_ · **Verificação:** _(preencher)_

---

### Fase F4 — Dispatcher (`dispatcher.py`)
**Objetivo:** o ciclo por minuto que dispara/reagenda/expira controles, correto (D5/D6).
- **Itens:**
  - `[sequencial]` `run_cycle()`: (1) recovery `processing>5min`; (2) lock atômico `UPDATE … SET processing=true … WHERE next_at<=now AND status='active' AND processing=false RETURNING *`; (3) grace window (config) → `status='cancelled'` + log; (4) por controle: montar `ctx` (F3), avaliar a árvore do passo (F1) com dados **frescos**.
  - `[sequencial]` **falso** → `tentativas_passo+1`; `< max_tentativas` e dentro do `deadline` → reagenda o **mesmo** passo por `proximo_instante`; senão `status='expired'` + log (**D5 — nunca avança fingindo progresso**).
  - `[sequencial]` **verdadeiro** → escolher mensagens (rotação A/B via `UPDATE proxima_mensagem_index+1 RETURNING`) → chamar `actions` → contar disparo **só** se ≥1 sucesso (**D6**) → agendar próximo passo por `proximo_instante` → `completed` se não houver.
- **Pronto quando:** com um controle semeado (`next_at=now`) e filtro que bate, o ciclo dispara e agenda o próximo; com filtro que não bate, **reagenda o mesmo** e após o teto **expira** (verificado em teste + `plugin_retornos_log`).

#### Status de execução — Fase F4
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_ · **Como foi feito:** _(preencher)_ · **Problemas:** _(preencher)_ · **Verificação:** _(preencher)_

---

### Fase F5 — Ações / envio (`actions.py`)
**Objetivo:** executar cada tipo de mensagem reusando o core; janela 24h → nota privada.
- **Itens:**
  - `[sequencial]` Re-derivar o canal por `conversation_repo` a cada disparo (⚠️ nunca deixar `add_message` resolver a inbox — precedente `agendamento_retorno/logic.py:384`).
  - `[paralelo]` `text`/mídia → `outbound_router.send_text/send_media` + `agent_handler.save_operator_message(reopen=False)`.
  - `[paralelo]` `private_note` → `ContactMemory.add_message('private_note', …, reopen=False)` + broadcast `new_message`.
  - `[paralelo]` `ia_responde_agora` → checar gates (humano assumiu? tag transferido?) com dados frescos; `agent_handler.aprocess_message(phone, instrução, save_user_message=False, save_response=False, channel_id=…)`; enviar as partes (reuso da lógica de `_run_private_ai`, `server/routes/contacts.py:1160`).
  - `[sequencial]` **Gate D3**: se `outbound_router.session_open(channel_id, last_inbound_ts) == False` → **substituir** a ação por uma `private_note` de aviso ("janela 24h expirou — cliente {nome} parado, reative manualmente").
  - `[sequencial]` Coroutines do `conversation_service` via `_run_on_loop` (`run_coroutine_threadsafe`, `logic.py:44`), pois o dispatcher roda em thread.
- **Pronto quando:** cada tipo entrega no destino certo; dentro da janela `ia_responde_agora` gera e envia; fora da janela vira nota privada; falha de um envio não derruba os outros.

#### Status de execução — Fase F5
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_ · **Como foi feito:** _(preencher)_ · **Problemas:** _(preencher)_ · **Verificação:** _(preencher)_

---

### Fase F6 — Eventos + lifecycle + settings (`events.py` · `lifecycle.py` · `settings.py`)
**Objetivo:** o cliente respondendo cria/reseta a régua; a IA/humano assumindo cancela; o scheduler roda.
- **Itens:**
  - `[sequencial]` `events.py` — `message.saved` (direção in, role `user`): se não há controle e alguma régua ativa (por `posicao`) tem `filtros_entrada` que batem → **criar** controle + agendar passo 1; se controle `active` → **`on_reply`** (`reset`|`cancel`, D8), gravando `last_client_ts`.
  - `[sequencial]` Cancelamento configurável (D8): assinar `conversation.status_changed`/`resolvida`/`arquivada` e `contact.ai_toggled`/atribuição a humano → `cancel_on_resolve`/`cancel_on_assign_human` da régua. **Confirmar os nomes reais dos eventos** em `agent/message_listeners.py` e no CLAUDE.md §Events (F6).
  - `[paralelo]` `lifecycle.py` — `setup(ctx)`: `actions.set_loop(loop)`, `ctx.spawn_task('scheduler', _loop, policy=PERMANENT)` (tick 60s → `asyncio.to_thread(dispatcher.run_cycle)`); `app.startup` recovery de locks órfãos. Base: `retorno_automatico/lifecycle.py`.
  - `[paralelo]` `settings.py` — delay entre msgs, grace minutes, expediente default, "aplicar a grupos".
- **Pronto quando:** enviar msg de cliente numa conversa que casa a régua cria um controle; responder de novo reseta; resolver/atribuir a humano cancela (conforme flags); `retornos:scheduler` aparece em `GET /api/runtime/tasks`.

#### Status de execução — Fase F6
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_ · **Como foi feito:** _(preencher)_ · **Problemas:** _(preencher)_ · **Verificação:** _(preencher)_

---

### Fase F7 — API REST (`routes.py` + `/metadata`)
**Objetivo:** todos os endpoints que a UI consome.
- **Itens:**
  - `[paralelo]` CRUD `reguas` (+ `reorder`, `duplicate` criada `ativo=false`), `passos`, `mensagens`; **rotas fixas antes das `:id`** (lição do Nexus e do NestJS — mesma armadilha no FastAPI por ordem de include).
  - `[paralelo]` `monitor` — listar controles (filtros por status), `reset`, `cancel`, `stats`.
  - `[paralelo]` `export`/`import` JSON de régua (árvore compatível com o Nexus — §5.1).
  - `[sequencial]` `GET /metadata` — catálogo de campos+operadores (de `catalog.py`) + inboxes + usuários/agentes + etiquetas de conversa + tags de contato + canais + agentes IA (lidos dos repos do core; 1 fetch por sessão no cliente).
  - `[sequencial]` Gates `plugin_permission('view'|'edit'|'monitor')` (`plugins/context.py`).
- **Pronto quando:** `curl` em cada rota retorna `{ok,data}`; `/metadata` traz as listas reais da instância.

#### Status de execução — Fase F7
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_ · **Como foi feito:** _(preencher)_ · **Problemas:** _(preencher)_ · **Verificação:** _(preencher)_

---

### Fase F8 — Construtor de regras recursivo (`static/RegraBuilder.js`)
**Objetivo:** a UI do print do Nexus, em Preact/HTM, com grupos aninhados.
- **Itens:**
  - `[sequencial]` `RegrasList` recursivo → renderiza mix de `CondicaoRow` e `GrupoBlock`; grupo com **borda tracejada**; botão **E/OU** que alterna (a partir da 2ª linha); `+ Adicionar condição` / `+ Adicionar grupo`; `X` para remover.
  - `[sequencial]` `CondicaoRow` — select de campo **agrupado** (Atendimento/Virtual/Módulo), select de operador filtrado pelo campo, `ValorInput` por tipo.
  - `[paralelo]` `ValorInput` — text/number/date/time/enum/select/multi-select/`between`(2 inputs, com dica "atravessa meia-noite" quando `min>max`). Reusar por URL absoluta `OptionListSelect` (`/static/js/components/OptionListSelect.js`) e os inputs de `ConversationFilterDialog.js`.
  - `[paralelo]` Preview: rodar `rules.js` (F2) sobre um contexto de exemplo para mostrar "esta regra bateria agora? (sim/não)".
  - ⚠️ Modo escuro: classes `wa-*` e `.wa-field` (CLAUDE.md §Tema). ⚠️ HTM: sem crase em comentário dentro de `html\`...\``.
- **Pronto quando:** montar a árvore da régua real "Principal - API Meta" (grupo-dentro-de-grupo) na UI, exportar, e o JSON bater com o do banco do Nexus; preview coerente com o backend.

#### Status de execução — Fase F8
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_ · **Como foi feito:** _(preencher)_ · **Problemas:** _(preencher)_ · **Verificação:** _(preencher)_

---

### Fase F9 — Tela de réguas (`static/reguas.js`)
**Objetivo:** lista de réguas + editor completo (paridade visual com `/configuracoes` do Nexus).
- **Itens:**
  - `[paralelo]` Lista de réguas (nome, badge Meta/janela, nº de passos, status, ativar/desativar, duplicar, excluir com confirmação, reordenar por DnD **HTML5 nativo** — sem dnd-kit).
  - `[sequencial]` Editor: cabeçalho da régua (`filtros_entrada` via `RegraBuilder`, `on_reply`, cancelamentos, expediente/tz), accordion de passos (reordenar por DnD), cada passo com `RegraBuilder` + editor de mensagens (tipos do §7; upload de mídia → `statics/outbox` do core ou endpoint do plugin — **confirmar** em F9).
  - `[paralelo]` Export/import JSON.
- **Pronto quando:** criar do zero uma régua com 4 passos (como a de produção), salvar, recarregar e reabrir intacta; legível no modo escuro.

#### Status de execução — Fase F9
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_ · **Como foi feito:** _(preencher)_ · **Problemas:** _(preencher)_ · **Verificação:** _(preencher)_

---

### Fase F10 — Monitor (`static/monitor.js`)
**Objetivo:** enxergar e operar os controles em andamento.
- **Itens:**
  - `[paralelo]` Tabela de controles (conversa, régua, passo atual, disparos, próximo disparo, status), filtros por status, ações reset/cancelar.
  - `[paralelo]` Atualização ao vivo por `wsBus.subscribe` (não `setInterval`), escutando um broadcast do plugin (ex.: `retornos_tick`).
- **Pronto quando:** um disparo/skip aparece no monitor sem reload; reset/cancelar refletem no banco.

#### Status de execução — Fase F10
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_ · **Como foi feito:** _(preencher)_ · **Problemas:** _(preencher)_ · **Verificação:** _(preencher)_

---

### Fase F11 — Aposentar `retorno_automatico` + seed opcional
**Objetivo:** evitar nota duplicada e (opcional) já entregar uma régua-espelho do comportamento antigo.
- **Itens:**
  - `[sequencial]` Na instalação/boot do `retornos`, **desativar** `retorno_automatico` (`plugin_repo.set_enabled('retorno_automatico', False)` — **confirmar** a API em F11) e logar. `agendamento_retorno` fica intacto (D1).
  - `[paralelo, opcional]` Seed de uma régua "Silêncio do cliente" replicando o comportamento do `retorno_automatico` (silêncio N horas dentro do expediente → nota privada), para migração sem perda de função.
- **Pronto quando:** com `retornos` ativo, `retorno_automatico` está `enabled=0`; nenhuma nota duplicada numa conversa de teste.

#### Status de execução — Fase F11
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_ · **Como foi feito:** _(preencher)_ · **Problemas:** _(preencher)_ · **Verificação:** _(preencher)_

---

### Fase F12 — Testes e2e + empacotamento + docs
**Objetivo:** fechar com verde e um `.zip` importável.
- **Itens:**
  - `[sequencial]` Teste e2e: msg do cliente → cria controle → `next_at=now` → ciclo → nota/IA na conversa; reset ao responder; cancelar ao resolver.
  - `[paralelo]` Gerar `.zip` (`GET /api/plugins/retornos/export`) para o repo `whatsbot-pro-plugins` (memória [[whatsbot-pro-plugins-repo]]).
  - `[paralelo]` README do plugin + nota no CLAUDE.md se necessário.
- **Pronto quando:** `venv/bin/python -m pytest tests/ -q` verde no Postgres de teste; `node --test` verde nos módulos puros; `.zip` importa e o plugin sobe sem `load_error`.

#### Status de execução — Fase F12
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_ · **Como foi feito:** _(preencher)_ · **Problemas:** _(preencher)_ · **Verificação:** _(preencher)_

---

## 9. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Motor de regras recursivo (Python **e** JS) | Divergência sutil entre os dois ⇒ preview mente | Mesmos vetores de teste nos dois; `rules.js` é porte 1:1 de `rules.py`; F1/F2 na mesma wave |
| Dispatcher em thread chamando coroutines | Deadlock / conversa em inbox errada | `_run_on_loop` (`run_coroutine_threadsafe`) + re-derivar canal por `conversation_repo` (precedente `agendamento_retorno`) |
| Loop infinito de eventos | `ia_responde_agora` gera `message.sent` que dispara o próprio plugin | O plugin **não** assina `message.sent` para agir; só `message.saved` (role user). Ver CLAUDE.md §Boas práticas |
| Nota/mensagem duplicada com `retorno_automatico` | Os dois rodam nos mesmos candidatos | F11 desativa `retorno_automatico` na instalação (D1) |
| Ordem de migration / prefixo | Migrator recusa objeto fora do prefixo; splita por `;` | `plugin_retornos_*` em tudo; sem `;` em comentário (memória [[plugin-migrator-splits-sql-by-semicolon]]) |
| Fuso horário | Container UTC desloca horários em 3h (bug do Nexus) | Offset fixo por régua (D9), nunca `datetime.now()` naïve |
| Janela 24h | Texto livre fora da janela falha (131047) só depois | Gate `session_open` **antes** do envio → nota privada (D3) |
| Modo escuro | Telas novas ilegíveis | `wa-*`/`.wa-field`; testar com dark ligado (CLAUDE.md §Tema) |
| HTM | Crase em comentário quebra o módulo em silêncio | `node --input-type=module` no check; `window.__whatsbotExtensions` p/ debug (memória [[htm-template-crase-quebra-modulo]]) |
| Restart de plugin | `enable/disable` chama `os._exit`; sem supervisor não volta | Dev usa `--reload`; prod Docker `restart: unless-stopped` (CLAUDE.md §Gotchas) |
| Lista×contagem no monitor | Filtrar no cliente sobre página paginada diverge da contagem | Contar no servidor (memória [[lista-contagem-divergem-filtro-cliente]]); monitor lê do banco, não só das rows carregadas |
| Persistência de `storages/` | Redeploy sem Persistent Storage zera o plugin do disco | Dados/config sobrevivem no Postgres; re-importar o `.zip` recupera (CLAUDE.md §Gotchas) |

---

## 10. Perguntas em aberto

**P1 — Onde versionar o plugin?**
⏸️ **RECOMENDADO (a decidir):** `assets/plugin_examples/retornos/` **deste** repo (como `protocolos`/`melhorias`), com `.zip` gerado dali — a complexidade (motor + scheduler + 2 telas) justifica review e testes junto do core. Alternativa (a): só no `whatsbot-pro-plugins` (padrão dos plugins não-core, sem histórico/review). O plano assume a recomendação; mudar só troca o caminho-base.

**P2 — Roteamento: `filtros_entrada` explícito na régua vs. filtros do 1º passo (cópia do Nexus)?**
✅ **DECIDIDO (2026-07-23, embutido no §7/D-implícito):** campo **`filtros_entrada` explícito** na régua (gate de entrada separado do passo 1). Corrige a maior confusão do Nexus (config sem filtro `chatwoot.*` no R1 virava catch-all acidental) sem custo extra. Se você preferir a cópia fiel (R1 = gate), é trocar o gate em F6/F9.

**P3 — Uma régua por conversa (como o Nexus) ou várias simultâneas?**
✅ **DECIDIDO:** **uma** por conversa (`conversation_id UNIQUE` no controle), igual ao Nexus. Várias simultâneas é muito mais complexo e não há demanda.

**P4 — `chatwoot.time_id` (Times) e `atendimentos.priority` existem no WhatsBot?**
⏸️ **A CONFIRMAR em F1** (grep em `db/tables.py`). Se "Times" não existir, **omitir** do catálogo (import que o referencie ⇒ condição false, não quebra). `priority` provavelmente existe (`db/filters/registry.py` tem a dim `priority`).

**P5 — `ia_responde_agora` deve respeitar o interruptor GLOBAL `auto_reply`?**
⏸️ **RECOMENDADO:** **não** re-checar `auto_reply` (a régua ativa **é** a intenção de responder), mas **checar sempre** os gates por-conversa (humano assumiu / tag `transferido_atendente`) com dados frescos no disparo. É o subconjunto do `list_candidates` do `retorno_automatico`. Confirmar com o usuário se quiser que o botão global também silencie as réguas.

---

## 11. Apêndice — arquivos-chave

**Novos (plugin `retornos`)** — todos sob `assets/plugin_examples/retornos/` (P1):
- Backend: `plugin.yaml` · `migrations/001_initial.sql` · `rules.py` · `catalog.py` · `schedule.py` · `evalctx.py` · `repo.py` · `dispatcher.py` · `actions.py` · `events.py` · `lifecycle.py` · `settings.py` · `routes.py`
- Frontend: `static/rules.js` · `static/rules.test.js` · `static/RegraBuilder.js` · `static/reguas.js` · `static/monitor.js` · `static/extends.js` (opcional)
- Testes core: `tests/test_retornos_rules.py` · `tests/test_retornos_dispatcher.py` · `tests/test_retornos_endpoints.py`

**Core que o plugin CONSOME (não altera):**
- `plugins/context.py:316` (`spawn_task`) · `runtime/supervisor.py:27` (`RestartPolicy`)
- `channels/outbound.py:45,48,80,91` (`supports`/`session_open`/`send_text`/`send_media`)
- `db/repositories/message_repo.py:267` (`last_inbound_ts`)
- `app/services/conversation_service.py:177,245,402,523,332` (`set_status`/`archive`/`assign`/`set_ai`/`_clear_transfer_tag`)
- `server/routes/contacts.py:1160` (`_run_private_ai` — padrão de `aprocess_message`)
- `agent/memory.py:389` (`add_message('private_note')`) · `agent/handler.py` (`aprocess_message`/`save_operator_message`)

**Referências de porte (Nexus, `/opt/nexus/nexus-retorno`):**
- `server/modules/retornos/filtros-engine.service.ts` · `filtros-metadata.ts` · `filtros.types.ts`
- `server/modules/dispatcher/dispatcher.service.ts` · `server/modules/chatwoot-webhook/chatwoot-webhook.service.ts`
- `src/components/filtros/{FiltrosBuilder,CondicaoRow,ValorInput}.tsx` · `src/app/configuracoes/[slug]/page.tsx` · `src/app/monitor/page.tsx`
- `docs/ARQUITETURA.md` · `docs/INVESTIGACAO-RETORNOS-NAO-ENVIADOS.md` · `docs/TODO-wrap-around-between.md`

**Plugins-precedente instalados (espinha dorsal a copiar):**
- `storages/plugins/agendamento_retorno/{logic,lifecycle,routes,settings}.py` + `static/{ScheduleTabs,extends}.js`
- `storages/plugins/retorno_automatico/{logic,lifecycle,schedule,settings}.py`

---

## 12. Checklist de verificação (por mudança)

- [ ] `venv/bin/python -m pytest tests/test_retornos_rules.py -q` verde (motor Python)
- [ ] `node --test` verde em `static/rules.test.js` (motor JS) e paridade com o Python
- [ ] Suíte geral verde no Postgres de teste (`WHATSBOT_TEST_DB_URL`) — `pytest tests/ -q`
- [ ] Migration round-trip: aplica em banco limpo, cria só `plugin_retornos_*`, `downgrade` limpo (se houver)
- [ ] Plugin sobe sem `load_error` em `/plugins`; `retornos:scheduler` aparece em `GET /api/runtime/tasks`
- [ ] Régua real "Principal - API Meta" (grupo-dentro-de-grupo) reproduzida na UI e export = JSON do Nexus
- [ ] Filtro falso → **reagenda o mesmo passo** e depois **expira** (D5); disparo conta **só** com sucesso (D6)
- [ ] `between 16:00..07:30` (wrap-around) funciona **sem** o workaround de grupo OU (D7)
- [ ] Fora da janela 24h → **nota privada** (D3); dentro → `ia_responde_agora` gera e envia
- [ ] `on_reply`/cancelamentos realmente aplicados (D8); cliente respondendo reseta/cancela conforme régua
- [ ] `retorno_automatico` **desativado** com o `retornos` ativo (D1) — sem nota duplicada
- [ ] Telas novas legíveis no **modo escuro** (`wa-*`/`.wa-field`)
- [ ] Restart de plugin (enable/disable) recupera; sem segredo em URL/argv
- [ ] `.zip` exportado importa numa instância limpa e o plugin funciona
