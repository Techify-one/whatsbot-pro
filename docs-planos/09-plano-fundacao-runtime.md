# Plano de Implementação — 09: Fundação de Runtime (lifecycle de plugin, supervisor de tasks, subprocesso gerenciado)

> Plano de execução derivado de [`docs-pesquisa/00-visao-geral.md`](../docs-pesquisa/00-visao-geral.md) e
> de [`docs-pesquisa/02-canais-e-providers.md`](../docs-pesquisa/02-canais-e-providers.md) §3.4.
>
> **Escopo deste plano — a FUNDAÇÃO transversal.** Detalha, em profundidade, as **três capacidades de
> runtime CORE** que destravam os providers-plugin (e qualquer plugin que precise "ligar" algo em
> runtime):
>
> 1. **Lifecycle de plugin aguardado** — ganchos `setup(ctx)` / `teardown(ctx)` chamados E **aguardados**
>    pelo host (no `app.startup`/`app.shutdown` e no enable/disable), com registro estilo Disposable
>    (`ctx.on_unload(...)`) executado mesmo se o `setup` falhar; e **parar de matar o processo antes do
>    teardown rodar**.
> 2. **Supervisor de tasks de fundo** — registry de corrotinas de longa duração (loop com `stop_event`),
>    com restart classificado + backoff/rate-limit (padrão OTP), acessível a core E plugins via o
>    `context`. Generaliza as 4 tasks hoje hardcoded no lifespan.
> 3. **Serviço de subprocesso gerenciado** — `Popen` em **process group** + **die-with-parent**
>    (`PR_SET_PDEATHSIG` no Linux) + parada graciosa **SIGTERM→timeout→SIGKILL** que **mata a árvore**
>    (`os.killpg`) + **PID-file / stale-kill no boot** + watchdog com rate-limit + readiness probe.
>    Acessível a core E plugins.
>
> **Escopo de plataforma (P29 — DECIDIDO 2026-06-19):** este plano é **só Linux/Docker**. Windows está
> **fora do escopo** do Pro por enquanto. O die-with-parent usa **`PR_SET_PDEATHSIG`** (Linux); o Job
> Object do Windows está **ADIADO** (só implementar se voltarmos a empacotar EXE). O stale-kill no boot
> continua valendo e cobre o pior caso.
>
> **Por que este plano existe separado do 02 (Canais).** O [`02-plano-canais-e-providers.md`](02-plano-canais-e-providers.md)
> referencia estas três capacidades mas as trata como pré-requisito. Este plano **consolida e detalha** a
> construção delas — é o que precisa estar pronto ANTES do GOWA virar provider-plugin. **As três são CORE
> e não podem ser plugin** (são a infraestrutura que os plugins consomem; problema do ovo-e-galinha —
> §3.4.4 do doc de pesquisa). O que vira plugin é o *provider/consumidor*, que usa estas capacidades pelo
> `context` injetado.
>
> **NÃO está no escopo deste plano** (vive em outros planos): o contrato `Channel`/`ChannelRegistry`,
> roteamento de webhook por canal, tabelas `channels`/`channel_credentials`, extração do GOWA e
> multi-número — tudo em [`02-plano-canais-e-providers.md`](02-plano-canais-e-providers.md). Modelo de
> inbox/conversa — [`01`](01-plano-inbox-e-conversas.md). RBAC — [`03`](03-plano-rbac-usuarios.md). Este
> plano entrega a **plataforma**; o plano 02 a **consome**.

---

## Estado atual (WF1, 2026-06-20)

> Verificação `arquivo:linha` do **WF1** (subagente read-only do plano 09, ver
> [`_RECONCILIACAO-WF1.md` §"Plano 09"](_RECONCILIACAO-WF1.md)). Working tree em `b673a61`. **Precedência:
> código real > capability-map > relatório > DECISOES > planos.** Os offsets aqui foram **re-ancorados por
> grep** no estado atual do código; números de linha são aproximados e devem ser confirmados por `grep` na
> implementação (drift conhecido — todos os planos foram escritos sobre um snapshot pré-AGNO).

**Resumo:** este plano é **greenfield (6/6 fases `nao_feito`)** — nenhum artefato existe ainda — porém é
**FUNDACIONAL e de Onda 1**: não depende de nenhum outro plano para começar, e destrava a Onda 2 (retrofit
P62) e parte do plano 02 (extração do GOWA). **Não é "completar o que já existe"; é construir a plataforma
do zero, mas com pontos de integração concretos no código atual.** Por isso cada fase abaixo já aponta o
`arquivo:linha` exato que será **endurecido** (não reescrito do zero — o GOWA, o lifespan, o loader e o
restart já existem e ficam funcionando; o que muda é a infraestrutura por baixo).

### Legenda de fases (estado verificado WF1)

| Fase | Entrega | Estado | O que JÁ existe no código | O que FALTA construir |
|---|---|---|---|---|
| **1** | Lifecycle `setup/teardown` aguardado + `ctx.on_unload` | **nao_feito** | `loader.py:33` (`LoadedPlugin`) e `loader.py:188` (`_load_plugin_module`) **não** reconhecem `entry.lifecycle`; nenhum gancho é chamado pós-import | módulo `plugins/lifecycle.py`, `PluginContext`, `on_unload`, captura de `setup/teardown`, wiring no lifespan |
| **2** | Fim do `os._exit` cego: teardown antes do hard-exit | **nao_feito** | `restart.py:42` (`schedule_restart`) toca o trigger e faz `os._exit(0)` em `:78` **sem** rodar teardown; `routes/plugins.py:95/106/130` chamam `schedule_restart` sem pré-saída | param `on_before_exit` em `schedule_restart`, passar teardown no disable/delete, ordem de shutdown no lifespan |
| **3** | Supervisor de tasks: registry + restart classificado + backoff | **nao_feito** | 4 tasks hardcoded em `app.py:188-191`, canceladas em `:198` **sem `await`**; cada corrotina em `server/background.py` engole erro no try/except interno | pacote `runtime/` + `runtime/supervisor.py`, migrar as 4 tasks, `task.crashed` em `KNOWN_EVENTS` |
| **4** | Serviço de subprocesso: process group + die-with-parent + stale-kill + readiness + watchdog | **nao_feito** | `gowa/manager.py:122` (`Popen` cru, só `CREATE_NO_WINDOW` em `:102`); `stop()` em `:137` faz `terminate()`/`kill()` no PID direto (não mata a árvore); watchdog 3/60s em `:169` | `runtime/subprocess_service.py`, `runtime/_proc_platform.py`, delegação do GOWA, `subprocess.crashed/restarted` em `KNOWN_EVENTS` |
| **5** | Exposição via `context` + observabilidade (health só em memória — P30) | **nao_feito** | `context.py:34` (`set_runtime` só recebe `ws_manager`+`loop`); `ToolContext` em `:74` não expõe loop/cleanup/spawn | `spawn_task`/`spawn_subprocess`, `set_runtime` estendido, `server/routes/runtime.py`, `RuntimePanel.js` |
| **6** | Provider de teste valida (i)+(ii) ponta-a-ponta | **nao_feito** | — (nenhum plugin `runtime_probe`) | plugin de exemplo `runtime_probe` + roteiro manual + testes de unidade |

> **Premissa invertida (Lote 3) — atualização vs. a redação original deste plano.** A versão anterior
> tratava o `tool_runner` code-in-DB (plano 06) como algo que **ainda não existia** e marcava o reuso do
> `SubprocessService` por ele como **ADIADO (P67)**. **As duas coisas mudaram:**
> 1. **O `tool_runner` code-in-DB JÁ EXISTE in-process** — `agent/ai_tool_installer.py:18` documenta a
>    SECURITY DEBT (P62): `exec_module` (`:77`) roda o Python do banco **in-process**, com todos os
>    privilégios. Está **mitigado por padrão** pelo kill-switch `ai_tools_code_enabled` (default **OFF**,
>    env `WHATSBOT_AI_TOOLS_CODE`) e pela tool nascendo `enabled=False` (gate humano P63 real) — os 5
>    arquivos do commit do kill-switch P62 (`config/settings.py`, `server/app.py`,
>    `server/routes/ai_engine.py`, `server/routes/config.py`, `agent/ai_tool_installer.py`).
> 2. **P67 NÃO está mais ADIADO** — virou **RETROFIT** (Lote 3, `DECISOES.md:169,258-266`): quando este
>    plano entregar a Fase 4 (`SubprocessService`), o instalador code-in-DB **migra** de `exec_module`
>    in-process para subprocesso isolado (RLIMIT + timeout). Isso cria a **dependência cruzada 06⇄09 =
>    Onda 2** (o retrofit, ver §"Posicionamento de onda").
>
> **Consequências de doc:** o "checklist P0 — gate admin-only" do `_REAVALIACAO-relatorio.md §6` está
> **OBSOLETO** (a mitigação P62 shipou depois dele). O que resta do P62 é (a) **RBAC** (plano 03, separação
> de papéis) e (b) **isolamento por subprocesso** (este retrofit). Ver detalhe na §"Retrofit P62/P67".

### Posicionamento de onda (sequência viva — relatório §4 / [`_RECONCILIACAO-WF1.md §1.3`](_RECONCILIACAO-WF1.md))

| Onda | Conteúdo | Relação com este plano |
|---|---|---|
| **0** | Endurecimento do que já shippou (pinar `agno`, popular `executions`, `server/dev.py`, `CLAUDE.md`) | Pré-requisito leve; o kill-switch P62 já está feito |
| **1** | **ESTE PLANO (09), Fases 1–4 → `SubprocessService`** | A maior alavanca técnica do Pro |
| **2** | **Retrofit P62/P67** — isolar o `ai_tool_installer` sobre o `SubprocessService` | **Consome a Fase 4 deste plano** (ver §"Retrofit P62/P67") |
| **3** | RBAC (03) + Inbox (01) | RBAC entrega o `admin-only` real para `/api/runtime/*` |
| **4** | Completar o plano 06 (binding agente↔inbox, handoff/routing, UI AI) | — |
| **5+** | 02 (canais/extração GOWA), 04 (quick replies), 05 (custom attrs), 08 (filtros, após 01/05/03) | A extração do GOWA (02) **consome a Fase 4 deste plano** |

> **A Fase 4 (SubprocessService) é a maior alavanca** do roadmap inteiro: é **consumida** tanto pela
> extração do GOWA-para-plugin (plano 02, Onda 5+) quanto pelo retrofit de segurança do code-in-DB
> (plano 06, Onda 2). Também é a de **maior risco** (stale-kill errado pode matar um PID reciclado e
> derrubar a sessão WhatsApp). Por isso a sequência interna é (i)→(ii) validadas num caso barato **sem
> subprocesso** (fase 6) antes de encostar em (iii).

---

## Decisões aplicadas (fonte: [`DECISOES.md`](DECISOES.md), incl. Lote 3 de 2026-06-19)

Este plano já incorpora as decisões abaixo no corpo (não são mais perguntas em aberto — **não re-litigar
decisão fechada**):

- **P22 / P25** — disable de plugin e toggle = **restart-do-processo**, com **teardown aguardado antes do
  `os._exit`**. Hot-unload fica para o futuro.
- **P26** — supervisor e serviço de subprocesso vivem num **novo pacote `runtime/`** (`runtime/supervisor.py`,
  `runtime/subprocess_service.py`, `runtime/_proc_platform.py`). Atualizar a árvore de Arquitetura no
  CLAUDE.md ao implementar.
- **P27** — supervisor usa **`task.cancel()` nativo**; `state.stop_event` global é mantido **só por compat**
  na transição das 4 corrotinas legadas.
- **P28** — **emitir no bus** os eventos `task.crashed`, `subprocess.crashed`, `subprocess.restarted`
  (apenas na transição; nunca dentro do crash-loop).
- **P29** — **só Linux/Docker**; die-with-parent via `PR_SET_PDEATHSIG`; Job Object/Windows **adiado**.
- **P30** — health de tasks/subprocessos **só em memória** no MVP (sem tabelas core, **sem migration**).
- **P31** — teardown com **timeout fixo (~10s)** por plugin, depois segue (com `os._exit` como rede final).
- **P62** — **kill-switch JÁ FEITO** (mitigação imediata do RCE do code-in-DB; default OFF). O que falta é o
  **isolamento por subprocesso = retrofit** sobre o `SubprocessService` deste plano (Onda 2). O instalador
  code-in-DB já existe **in-process** (`agent/ai_tool_installer.py`).
- **P67** — **NÃO está mais ADIADO** (Lote 3): virou **retrofit** — reusar o `SubprocessService` deste plano
  para o `tool_runner` do plano 06. Premissa invertida: o runner já existe in-process.
- **P64** (não é deste plano, mas afeta o sequenciamento do 06 que faz o retrofit) — **rebaixado** a fase
  futura opcional; o split usa JSON manual endurecido pelo PR #8. Não é requisito de fase inicial.
- **P65** — **cumprida** no sentido "o motor de raciocínio É o AGNO" (loop OpenAI removido); o config-in-DB
  (`agent_factory`) segue atrás da flag `ai_engine_enabled` (default OFF). "Agno-first" = motor, não
  config-in-DB default.

---

## 0. Estado atual do código (baseline real, verificado WF1 2026-06-20)

Os pontos exatos que este plano vai mexer (re-ancorados por `grep` no working tree atual; offsets
aproximados — confirmar com `grep` na implementação):

### Plugin loader e import
- **`plugins/loader.py`** — `LoadedPlugin` (~linha 33, antes citado 31-49) carrega `tools`, `prompt_fragments`,
  `event_handlers`, `filters`, `router`, `settings_cls`, `static_dir`. **Não há** `setup_fn`/`teardown_fn`,
  nem `background_tasks`, nem `subprocesses`. `_load_plugin_module` (~linha 188, antes 166-260) reconhece
  `entry.tools/prompts/events/filters/routes/settings` — **não existe** `entry.lifecycle`. O loader
  **só importa** o módulo; nenhum gancho é chamado depois.
- **`plugins/manifest.py`** — `PluginManifest.entry` é `dict[str,str]`. Sem campo de lifecycle.

### Lifespan e tasks de fundo
- **`server/app.py`** — `lifespan` (~linha 167, antes citado 142-183). Em `app.startup` seta runtime dos
  buses, emite `plugin.loaded`/`app.startup` (`:183`), e cria **4 tasks HARDCODED numa lista local**
  (`:188-191`): `asyncio.create_task(start_gowa_task(deps))`, `status_poll_loop`, `qr_poll_loop`,
  `avatar_fetch_task`. No `yield`/shutdown: emite `app.shutdown` (`:195`), seta `state.stop_event` (`:196`),
  faz `for task in tasks: task.cancel()` (`:198` — **sem `await` do término**, não espera as tasks
  pararem), `settings.save()`, `gowa_manager.stop()`. **Não há registry de tasks** ao qual um plugin se anexe.
- **`server/background.py`** — as 4 corrotinas. `status_poll_loop`/`qr_poll_loop`/`avatar_fetch_task`
  fazem `while not state.stop_event.is_set(): ...; await asyncio.sleep(N)`. **Cada uma trata seus
  próprios erros num try/except** que engole tudo e segue o loop — não há restart/backoff classificado;
  se a corrotina **morre por exceção fora do try interno**, ninguém a relança.

### Restart / toggle de plugin
- **`plugins/restart.py`** — `schedule_restart()` (~linha 42) toca `server/_reload_trigger.py` (`:39/:59`) e
  agenda `os._exit(0)` (`:78`) após um delay curto numa thread daemon. **`os._exit` pula
  finalizers/atexit/handlers** → um subprocesso aberto por um plugin viraria **órfão**, e nenhum
  `teardown` roda. A assinatura atual é `schedule_restart(reason: str = "")` — **sem hook de pré-saída**.
- **`server/routes/plugins.py`** — `enable_plugin` (~linha 87), `disable_plugin` (~linha 99),
  `delete_plugin` (~linha 110): setam `plugins.enabled` no DB, emitem `plugin.enabled`/`plugin.disabled`
  **antes** do restart (comentário em `:93`: "after the os._exit the bus is gone"), e chamam
  `schedule_restart(...)` (`:95/:106/:130`).

### Event bus
- **`plugins/events.py`** — `emit()` (~linha 129) usa `run_coroutine_threadsafe(_fanout(), _loop)` (`:172`)
  **sem `.result()`** → fire-and-forget. `app.startup`/`app.shutdown` estão em `_LIFECYCLE_EVENTS` (`:78`)
  e em `emit_with_filter` chamam `emit()` direto (`:190`). **O shutdown não aguarda os handlers de plugin
  terminarem** — um teardown via evento pode ser cortado.
- **`KNOWN_EVENTS`** (~linha 39): conjunto verificado — **não contém** `task.crashed`, `subprocess.crashed`
  nem `subprocess.restarted` (P28 pendente). Já contém `app.startup`/`app.shutdown`, `plugin.*`,
  `execution.started/ended`, etc.

### Context dos plugins
- **`plugins/context.py`** — `set_runtime(ws_manager, loop)` (~linha 34) + `broadcast()` (`:41`).
  `ToolContext` (`:74`), `PromptContext`, `EventContext`, `FilterContext` expõem `plugin_id`, `plugin_db`,
  `handler`. **Não expõem** o event loop, `stop_event`, nem registro de cleanup. Um plugin **não consegue**
  registrar uma task gerenciada nem um teardown.

### Subprocesso (GOWA, hoje cravado no core)
- **`gowa/manager.py`** — `GOWAManager`: 1 `subprocess.Popen` (~linha 122), **sem `start_new_session`
  / sem `creationflags` de process group** (só `CREATE_NO_WINDOW` no Windows, `:102`). `stop()` (~linha 137)
  faz `terminate()`→`wait(5)`→`kill()`→`wait(3)` **no PID direto** (`:145/:149`) — **não mata a árvore** nem
  usa process group. Watchdog em thread daemon (`:169`) com rate-limit 3/60s (`:181-200`). **Não há
  PID-file**, **não há stale-kill no boot**, **não há readiness probe** (só um health-check loop externo em
  `start_gowa_task`). Limpeza de órfãos é externa/grosseira (`pkill -f bin/gowa` em `linux_start.sh`).
- **`main.py`** — instancia `GOWAManager`/`GOWAClient` e chama `create_app(...)`. (No plano 02 isso
  migra; aqui só consumimos o serviço de subprocesso para endurecer o GOWA **sem** ainda movê-lo para
  plugin.)

### Code-in-DB já existente in-process (cria a dependência cruzada 06⇄09)
- **`agent/ai_tool_installer.py`** — instalador de tools code-in-DB. `:18` documenta a SECURITY DEBT
  (P62): `exec_module` (`:77`) roda o Python persistido no banco **in-process**, com todos os privilégios
  (DB, filesystem, network, chave LLM). **Mitigado por padrão** pelo kill-switch `ai_tools_code_enabled`
  (default OFF). **Este é o consumidor do retrofit P62/P67** (Onda 2): quando a Fase 4 entregar o
  `SubprocessService`, o `exec_module` migra para subprocesso isolado. Ver §"Retrofit P62/P67".

### Banco
- **`db/tables.py`** — `Table` objects (Core). Migrations Alembic em `db/alembic/versions/`. Este plano
  **NÃO adiciona tabelas core nem migrations** (P30 — health de tasks/subprocessos fica **só em memória**
  no MVP). **Cadeia Alembic real verificada** (head = `0008_plugin_installed_deps`):
  ```
  0001_baseline → 0002_message_revoked → 0003_message_reactions → 0004_message_reply_to
    → 0005_contact_pinned → 0006_contact_mention → 0007_ai_engine_tables → 0008_plugin_installed_deps  (HEAD)
  ```
  Os slots `0007` (AGNO) e `0008` (pkg_deps) **já foram consumidos**. Este plano não cria migration; se
  algum dia precisar (não no MVP), a regra **P82** se aplica: `down_revision` = head real no momento de
  implementar (hoje `0008_plugin_installed_deps`), número = próximo livre (≥ 0009). **Nunca** usar 0006/0007/
  0008 como slot novo — ramifica a cadeia e **quebra o boot**.

---

## Visão geral das fases

| Fase | Entrega | Capacidade | Critério de pronto (resumo) |
|---|---|---|---|
| **1** | Lifecycle de plugin: `setup/teardown` aguardados + `ctx.on_unload` (Disposable) | (i) | enable/disable e startup/shutdown chamam e **esperam** `setup`/`teardown`; cleanups rodam mesmo com setup falho |
| **2** | Fim do `os._exit` cego: graceful drain antes do hard-exit; teardown roda no disable | (i) | toggle de plugin roda `teardown` ANTES de derrubar; sem órfãos |
| **3** | Supervisor de tasks de fundo: registry + restart classificado + backoff | (ii) | as 4 tasks core migram pro supervisor; plugin registra task pelo `ctx` |
| **4** | Serviço de subprocesso gerenciado: process group + die-with-parent + stale-kill + readiness + watchdog | (iii) | GOWA roda pelo serviço; matar o pai mata o filho; boot mata instância stale |
| **5** | Exposição aos plugins via `context` + observabilidade (endpoints + WS, **health só em memória — P30**) | (i)(ii)(iii) | `ctx.spawn_task` / `ctx.spawn_subprocess` funcionam; UI mostra estado |
| **6** | Provider de teste "barato" valida (i)+(ii) ponta-a-ponta | validação | um plugin de teste sobe um loop gerenciado e o derruba limpo no disable |

> **Sequenciamento (decisão 2026-06-18, doc 02 §3.4.8):** (i)→(ii) primeiro, validadas num caso barato
> **sem subprocesso** (fase 6); (iii) por último. A fase 4 (subprocesso) só endurece o GOWA **no core**;
> mover o GOWA para plugin é o plano 02, que depende DESTE plano concluído. **O retrofit P62/P67 (plano 06)
> também depende da fase 4** — ver §"Retrofit P62/P67".

---

## Fase 1 — Lifecycle de plugin aguardado (`setup` / `teardown` + Disposable)

**Estado WF1:** `nao_feito`. **O que JÁ existe:** o loader importa o módulo do plugin mas **não chama nenhum
gancho** depois (`loader.py:188` `_load_plugin_module` reconhece `entry.tools/prompts/events/filters/routes/
settings`, nunca `entry.lifecycle`; `LoadedPlugin` em `:33` não tem `setup_fn`/`teardown_fn`). **O que
FALTA:** o ponto de inicialização/finalização garantido.

**Objetivo:** dar a todo plugin um ponto **garantido** de inicialização e — crucialmente — de
**finalização limpa**, no estilo `activate/deactivate` do VS Code (`context.subscriptions` →
Disposables) e `async_setup_entry/async_unload_entry` + `entry.async_on_unload(...)` do Home Assistant.

### Passos

1. **Manifest** — `plugins/manifest.py`:
   - Reconhecer `entry.lifecycle: lifecycle` (nome do módulo, ex. `storages/plugins/<id>/lifecycle.py`).
   - Sem mudança de schema persistido; `entry` continua `dict[str,str]`.

2. **Contrato de export** — o módulo `lifecycle.py` exporta duas funções (sync ou async):
   ```python
   def setup(ctx: PluginContext) -> None: ...      # ou async def
   def teardown(ctx: PluginContext) -> None: ...   # ou async def
   ```
   `setup` recebe um **`PluginContext`** novo (ver passo 4) e pode registrar cleanups via
   `ctx.on_unload(callable)`. `teardown` é opcional se o plugin só usa `on_unload`.

3. **Loader** — `plugins/loader.py`:
   - `LoadedPlugin` ganha `setup_fn: callable | None`, `teardown_fn: callable | None`.
   - `_load_plugin_module`: se `entry.lifecycle`, importar o submódulo e capturar `setup`/`teardown`.
   - **Importante:** o loader **não chama** `setup` (a importação continua síncrona, sem event loop). A
     chamada de `setup` acontece no lifespan/host, onde há loop (passo 5).

4. **`PluginContext` + registro de cleanup (Disposable)** — `plugins/context.py`:
   - Novo dataclass `PluginContext` com: `plugin_id`, `plugin_db`, `handler`, `loop` (o event loop),
     `broadcast` (ref), e um **registro de disposables interno**.
   - Método `ctx.on_unload(fn: Callable[[], None | Awaitable[None]])` — empilha cleanups. Executados em
     **ordem reversa** no teardown.
   - Método `ctx.spawn_task(...)` e `ctx.spawn_subprocess(...)` ficam para as fases 3 e 5 (assinaturas
     reservadas aqui).
   - Um **`PluginLifecycleManager`** (novo módulo `plugins/lifecycle.py`) mantém, por `plugin_id`: o
     `PluginContext`, a lista de disposables, e se o `setup` rodou com sucesso. Expõe
     `await run_setup(plugin_id, loaded)` e `await run_teardown(plugin_id)`.

5. **Host chama e AGUARDA** — `server/app.py` lifespan:
   - Em `app.startup`, **depois** de setar runtime dos buses (`set_runtime` já tem `_loop`), para cada
     `loaded` com `setup_fn`: `await lifecycle.run_setup(loaded.id, loaded)` — sync vai para
     `asyncio.to_thread`, async é `await`-ado. **Falha de `setup` não derruba o app**: loga, marca
     `load_error` no `plugins` repo, **executa os `on_unload` já registrados** (princípio HA: cleanup
     roda mesmo com setup falho) e segue.
   - No shutdown, **antes** de cancelar tasks core (hoje `app.py:198`): `await lifecycle.run_teardown(loaded.id)`
     para cada plugin carregado, com **timeout por plugin** (~10s, P31) para não travar o shutdown.

### Arquivos
- Criar: `plugins/lifecycle.py` (`PluginLifecycleManager`, `run_setup`, `run_teardown`).
- Editar: `plugins/manifest.py` (doc do `entry.lifecycle`), `plugins/loader.py` (capturar `setup`/`teardown`),
  `plugins/context.py` (`PluginContext` + `on_unload`), `server/app.py` (chamar/aguardar no lifespan).

### Critério de pronto
- Um plugin com `entry.lifecycle` tem `setup` chamado **uma vez** no startup (verificável por log) e
  `teardown` no shutdown — **e o shutdown espera** o teardown terminar (medível: um teardown que dorme
  2s atrasa o shutdown em ~2s, não é cortado).
- Um `setup` que lança exceção: app sobe normalmente, plugin marcado com `load_error`, e qualquer
  `on_unload` registrado antes da falha é executado (verificável por log).

---

## Fase 2 — Fim do `os._exit` cego: teardown antes do hard-exit

**Estado WF1:** `nao_feito`. **O que JÁ existe:** `restart.py:42` `schedule_restart(reason="")` toca o
trigger de reload e faz `os._exit(0)` em `:78` — **sem rodar teardown**; `routes/plugins.py` chama
`schedule_restart` no enable (`:95`), disable (`:106`) e delete (`:130`). **O que FALTA:** rodar o teardown
**antes** do processo morrer.

**Objetivo:** hoje o toggle de plugin (`enable`/`disable`/`delete`) chama `schedule_restart()` →
`os._exit(0)`, **pulando finalizers**. Um plugin que abriu loop/subprocesso vira órfão e o
`teardown` nunca roda. Precisamos rodar o teardown **antes** do processo morrer.

### Passos

1. **`plugins/restart.py`** — introduzir um **hook de pré-saída**:
   - `schedule_restart(reason="", on_before_exit: Callable[[], Awaitable[None]] | None = None)`.
   - Antes do `os._exit(0)`, se houver `on_before_exit`, **agendá-lo no event loop e aguardar** (com
     timeout fixo **~10s**, P31) — usar `asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)`. O
     loop é o que `plugins.context`/`plugins.events` já guardam (`_loop`).
   - **Atenção (risco WF1):** deadlock no `run_coroutine_threadsafe` se o loop já estiver parando.
     Proteger com timeout e fallback para o `os._exit` direto se o loop não responder.
   - Manter o touch do `_reload_trigger.py` (uvicorn --reload) e o `os._exit` como rede de segurança.

2. **Disable de plugin = restart-do-processo (P22 / P25 — DECIDIDO):**
   - **MVP:** disable continua sendo restart-do-processo, mas agora roda
     `lifecycle.run_teardown(plugin_id)` no `on_before_exit` **antes** do `os._exit`. Simples, seguro,
     supervisor relança.
   - **Futuro (hot-unload):** desregistrar tools/prompts/events/filters/tasks/subprocessos do plugin **em
     runtime** (estilo HA reload sem reiniciar). Requer desregistro reverso em todos os registries. Fora do
     MVP.

3. **`server/routes/plugins.py`** — `enable_plugin`/`disable_plugin`/`delete_plugin`:
   - Para **disable** e **delete**: passar `on_before_exit=lambda: lifecycle.run_teardown(plugin_id)` ao
     `schedule_restart`. (Para **enable** não há teardown a rodar.)
   - Manter a ordem: emitir `plugin.disabled` **antes** (já é o caso, `:105`), depois agendar restart
     com o teardown.

4. **Shutdown ordenado no lifespan (reforço da fase 1):** garantir que o shutdown rode, na ordem:
   (a) `emit("app.shutdown")`; (b) `lifecycle.run_teardown` de todos os plugins; (c) parar o supervisor
   de tasks (fase 3) — que cancela tasks core e de plugin; (d) parar subprocessos gerenciados (fase 4);
   (e) `settings.save()`.

### Arquivos
- Editar: `plugins/restart.py` (param `on_before_exit`), `server/routes/plugins.py` (passar teardown),
  `server/app.py` (ordem de shutdown).

### Critério de pronto
- Disable de um plugin que registrou um `on_unload` (ex. fecha um arquivo / loga "bye"): o cleanup roda
  **antes** do processo sair (verificável no log, antes da linha "Restarting now").
- Nenhum subprocesso/órfão sobra após disable de um plugin que tinha subprocesso (testável depois da
  fase 4).

---

## Fase 3 — Supervisor de tasks de fundo (registry + restart classificado)

**Estado WF1:** `nao_feito`. **O que JÁ existe:** 4 tasks hardcoded numa lista local do lifespan
(`app.py:188-191`), canceladas no shutdown **sem `await`** (`:198`); `server/background.py` tem as 4
corrotinas com try/except interno que engole erros. **O que FALTA:** o pacote `runtime/` e o supervisor.

**Objetivo:** generalizar as 4 tasks hardcoded do lifespan num **supervisor** onde core e plugins
registram corrotinas de longa duração, com **restart classificado** (`permanent`/`transient`/`temporary`,
padrão Erlang/OTP) e **rate-limit/backoff** (o watchdog do GOWA já faz uma versão disso: 3 restarts/60s).
**Esta capacidade, sozinha, habilita Telegram long-poll e e-mail/IMAP como plugin.**

### Desenho

Novo módulo **`runtime/supervisor.py`** (P26 — pacote `runtime/` dedicado):

```python
class RestartPolicy(Enum):
    PERMANENT = "permanent"   # sempre relança (default p/ loops de provider)
    TRANSIENT = "transient"   # relança só se terminou por exceção, não se saiu normal
    TEMPORARY = "temporary"   # nunca relança

@dataclass
class TaskSpec:
    name: str
    coro_factory: Callable[[], Awaitable[None]]   # cria uma NOVA coro a cada (re)start
    policy: RestartPolicy = RestartPolicy.PERMANENT
    max_restarts: int = 3
    window_sec: float = 60.0
    backoff_base: float = 2.0        # backoff exponencial entre restarts
    owner: str = "core"              # "core" ou plugin_id

class TaskSupervisor:
    def register(self, spec: TaskSpec) -> None: ...
    async def start_all(self) -> None: ...
    async def start(self, name: str) -> None: ...
    async def stop(self, name: str) -> None: ...          # cancela + aguarda
    async def stop_owner(self, owner: str) -> None: ...   # para todas dum plugin (disable)
    async def stop_all(self) -> None: ...                 # shutdown
    def status(self) -> list[dict]: ...                   # name, owner, state, restarts, last_error
```

Detalhes:
- O supervisor **envolve cada task** numa corrotina-guarda: `await coro()`; se sair por exceção e a
  policy permitir e o rate-limit não estourou, espera `backoff_base ** n` e **recria via `coro_factory`**
  (não reusa a coro consumida). Se estourar o rate-limit, marca `state="crashed"` e **emite `task.crashed`
  no bus** (P28; ver Fase 5) sem derrubar o app.
- **`stop`/`stop_owner`/`stop_all`** fazem `task.cancel()` **e `await`** com timeout (P27 — `cancel()`
  nativo é o mecanismo único de parada) — corrige o gap atual do lifespan (`app.py:198`) que cancela
  sem esperar.
- **`state.stop_event` só por compat (P27):** o supervisor usa `task.cancel()`/`CancelledError` como
  mecanismo de parada. As 4 corrotinas legadas continuam podendo checar `state.stop_event` na transição;
  serão migradas gradualmente para reagir a `CancelledError`. Sem `stop_event` por-task.

### Migração das 4 tasks core

- Em `server/app.py` lifespan, substituir a lista hardcoded (`app.py:188-191`) por:
  ```python
  supervisor.register(TaskSpec("gowa_start", lambda: start_gowa_task(deps), policy=TRANSIENT, ...))
  supervisor.register(TaskSpec("status_poll", lambda: status_poll_loop(deps), policy=PERMANENT, ...))
  supervisor.register(TaskSpec("qr_poll", lambda: qr_poll_loop(deps), policy=PERMANENT, ...))
  supervisor.register(TaskSpec("avatar_fetch", lambda: avatar_fetch_task(deps), policy=PERMANENT, ...))
  await supervisor.start_all()
  ```
- No shutdown: `await supervisor.stop_all()` (substitui o `for task in tasks: task.cancel()` de `:198`).
- `server/background.py` não muda de forma (as corrotinas continuam loops com `stop_event`); só passam a
  ser criadas via `coro_factory`. O try/except interno de cada uma pode **afrouxar**: erros fatais agora
  têm relançamento gerenciado, então não é mais obrigatório engolir tudo silenciosamente.
- **Atenção (risco WF1):** preservar a semântica do watchdog GOWA (3/60s) e da reconexão/QR — não
  regredir a reconexão automática ao migrar `start_gowa_task` para o supervisor.

### Exposição a plugins
- Reservar `ctx.spawn_task(name, coro_factory, *, policy=PERMANENT, ...)` no `PluginContext` que delega
  ao supervisor com `owner=plugin_id`. A task é automaticamente parada no `stop_owner` durante o
  teardown/disable do plugin (fase 1/2). Implementação concreta na fase 5.

### Arquivos
- Criar: `runtime/__init__.py`, `runtime/supervisor.py` (`TaskSupervisor`, `TaskSpec`, `RestartPolicy`).
- Editar: `server/app.py` (instanciar supervisor, registrar 4 tasks, start/stop), `plugins/context.py`
  (assinatura `spawn_task`), opcionalmente `server/background.py` (afrouxar try/except).
- Bus: adicionar `task.crashed` a `KNOWN_EVENTS` em `plugins/events.py` (~linha 39, P28).

### Critério de pronto
- As 4 tasks core rodam pelo supervisor (verificável em `supervisor.status()`).
- Uma task `PERMANENT` que lança exceção é **relançada** com backoff (verificável: matar a coro e ver o
  restart no log/status); ao estourar 3 restarts/60s, vira `crashed` sem derrubar o app.
- Shutdown **aguarda** todas as tasks pararem (medível: uma task que demora a cancelar atrasa o
  shutdown, não é cortada).

---

## Fase 4 — Serviço de subprocesso gerenciado

**Estado WF1:** `nao_feito`. **O que JÁ existe:** `gowa/manager.py` com `Popen` cru (`:122`, só
`CREATE_NO_WINDOW` em `:102`), `stop()` que mata o PID direto (`:137-149`, sem process group, sem matar a
árvore), watchdog 3/60s em thread daemon (`:169`), **sem PID-file/stale-kill/readiness** (limpeza de órfão
hoje é externa: `pkill -f bin/gowa` no `linux_start.sh`). **O que FALTA:** o serviço robusto e a delegação.

**Objetivo:** um dono **robusto** de subprocessos no core, que: sobe em **process group**, **morre-com-o-pai**
(defesa contra o hard-exit do toggle), **mata a árvore** no stop, faz **stale-kill no boot** (essencial
para não duplicar a sessão WhatsApp do GOWA), tem **watchdog com rate-limit** e **readiness probe**. O
core passa a usá-lo **para o GOWA** (endurecimento), e ele fica **exposto aos plugins** (o que habilita o
GOWA-como-plugin no plano 02 **e** o retrofit P62/P67 no plano 06).

### Desenho

Novo módulo **`runtime/subprocess_service.py`** (P26 — pacote `runtime/` dedicado):

```python
@dataclass
class SubprocessSpec:
    name: str                     # ex. "gowa" / "gowa:comercial"
    cmd: list[str]
    env: dict | None = None
    cwd: str | None = None
    pid_file: Path | None = None  # default: storages/run/<name>.pid
    readiness: Callable[[], bool] | None = None   # probe (ex. health_check HTTP)
    readiness_timeout: float = 15.0
    stdout: ... = DEVNULL          # mesma política do GOWA (DEVNULL/arquivo debug)
    max_restarts: int = 3
    window_sec: float = 60.0
    on_restart: Callable | None = None
    owner: str = "core"            # "core" ou plugin_id

class ManagedProcess:
    def start(self) -> None: ...        # stale-kill → Popen(grupo + die-with-parent) → readiness → watchdog
    def stop(self, timeout=5.0) -> None # SIGTERM ao grupo → wait → SIGKILL ao grupo
    def is_running(self) -> bool: ...
    def status(self) -> dict: ...

class SubprocessService:
    def spawn(self, spec: SubprocessSpec) -> ManagedProcess: ...
    def get(self, name: str) -> ManagedProcess | None: ...
    def stop_all(self) -> None: ...
    def stop_owner(self, owner: str) -> None: ...
```

### Técnicas (Linux/Docker — P29; macOS POSIX equivalente)

| Técnica | Linux/macOS (POSIX) |
|---|---|
| **Process group** | `Popen(start_new_session=True)` → novo grupo; matar com `os.killpg(os.getpgid(pid), SIG)` |
| **Die-with-parent** | `preexec_fn` chamando `prctl(PR_SET_PDEATHSIG, SIGKILL)` via `ctypes` (libc) — mata o filho se o pai morrer (cobre o `os._exit` do toggle). **macOS não tem `PR_SET_PDEATHSIG`** → recai só no stale-kill + stop explícito |
| **Stop gracioso** | `os.killpg(pg, SIGTERM)` → `wait(timeout)` → `os.killpg(pg, SIGKILL)` |
| **Stale-kill no boot** | ler PID-file; se o PID vivo casa com o cmd esperado, `killpg`/kill antes de subir |
| **Readiness probe** | rodar `spec.readiness()` num loop curto até `True` ou timeout (estilo pytest-xprocess) |

> **Windows ADIADO (P29):** Job Object com `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, `CREATE_NEW_PROCESS_GROUP`
> e `taskkill /T` ficam **fora do escopo** deste plano. Só voltam se reempacotarmos o EXE Windows. O
> alvo de referência é Linux/Docker, onde `PR_SET_PDEATHSIG` resolve o die-with-parent de forma limpa. O
> `CREATE_NO_WINDOW` que ainda existe em `gowa/manager.py:102` vira no-op de compat.
>
> **Atenção `preexec_fn`:** não é thread-safe em todos os cenários. Encapsular num helper
> `_posix_pdeathsig()` aplicado só em POSIX (no-op em plataformas sem `prctl`).

### PID-file e stale-kill
- Diretório novo `storages/run/` (gitignored). Cada subprocesso grava `storages/run/<name>.pid` com PID +
  uma assinatura do cmd (para evitar matar um PID reciclado por outro processo).
- No `start()`, **antes** do `Popen`: se o PID-file existe e o PID está vivo e a assinatura casa → matar
  (resolve nativamente o conflito de sessão WhatsApp que hoje depende do `pkill` externo dos launchers).
- **Atenção (risco WF1 — maior risco do plano):** stale-kill mal feito mata um PID reciclado por outro
  processo e pode **perder a sessão WhatsApp**. A assinatura do cmd no PID-file é a defesa; testar
  exaustivamente no Docker/Linux antes de confiar.

### Endurecer o GOWA com o serviço (sem mover para plugin ainda)
- **`gowa/manager.py`**: refatorar `GOWAManager` para **delegar** ao `SubprocessService`/`ManagedProcess`
  em vez de chamar `Popen`/`terminate` direto. Manter a API pública (`start`/`stop`/`restart`/`is_running`/
  `_on_restart`) para `main.py` e `server/app.py` não quebrarem.
  - `start()` monta o `cmd` como hoje (a montagem do comando GOWA já está no `start`) e cria um
    `SubprocessSpec` com `readiness=gowa_client.health_check`, `pid_file=storages/run/gowa.pid`,
    `on_restart=self._on_restart`.
  - O watchdog 3/60s (`gowa/manager.py:169-200`) passa a ser o do serviço (mesma semântica).
- Resultado imediato: matar o processo Python (mesmo via `os._exit`) **mata o GOWA** (die-with-parent); o
  boot **mata GOWA stale** sem depender dos scripts de launcher.

### Exposição a plugins
- Reservar `ctx.spawn_subprocess(spec)` no `PluginContext`, delegando ao `SubprocessService` com
  `owner=plugin_id`. Parado no `stop_owner` durante teardown/disable. Implementação concreta na fase 5.

### Arquivos
- Criar: `runtime/subprocess_service.py` (`SubprocessService`, `ManagedProcess`, `SubprocessSpec`),
  helper `runtime/_proc_platform.py` (pdeathsig POSIX; Windows ADIADO).
- Editar: `gowa/manager.py` (delegar ao serviço), `server/app.py` (instanciar serviço; parar no shutdown
  **antes** do `os._exit`), `.gitignore` (`storages/run/`), `plugins/context.py` (assinatura
  `spawn_subprocess`).
- Bus: adicionar `subprocess.crashed` e `subprocess.restarted` a `KNOWN_EVENTS` em `plugins/events.py`
  (~linha 39, P28; emitir só na transição).

### Critério de pronto
- Matar o processo Python com `kill -9` (ou simular o `os._exit` do toggle): o GOWA **morre junto**
  (die-with-parent via `PR_SET_PDEATHSIG`), verificável com `ps` no Linux/Docker.
- Iniciar o app com um GOWA órfão rodando: o boot **mata o stale** e sobe um novo sem conflito de sessão
  (verificável: QR/conexão normal; sem dois GOWAs no `ps`).
- `stop()` mata a **árvore** (subprocessos-filho do GOWA, se houver) — `os.killpg`.
- Readiness: `start_gowa_task` só declara "pronto" após o probe passar (sem o sleep-and-pray de 10
  iterações atual).

---

## Fase 5 — Exposição aos plugins via `context` + observabilidade

**Estado WF1:** `nao_feito`. **O que JÁ existe:** `context.py:34` `set_runtime(ws_manager, loop)` e
`broadcast` em `:41`; `ToolContext` em `:74` não expõe loop/cleanup/spawn. **O que FALTA:** a ponte
`spawn_*` e os endpoints/UI de observabilidade.

**Objetivo:** fechar a ponte entre as capacidades (i)(ii)(iii) e os plugins, e dar visibilidade
operacional (UI/endpoints) ao estado de tasks e subprocessos.

### Passos

1. **`PluginContext` completo** — `plugins/context.py`:
   - `ctx.spawn_task(name, coro_factory, *, policy=PERMANENT, max_restarts=3, window_sec=60)` →
     `supervisor.register(TaskSpec(name=f"{plugin_id}:{name}", ..., owner=plugin_id))` + start. Auto-parado
     no teardown via `stop_owner(plugin_id)`.
   - `ctx.spawn_subprocess(spec)` → `subprocess_service.spawn(spec, owner=plugin_id)`. Auto-parado no
     teardown.
   - `ctx.on_unload(fn)` (da fase 1) cobre cleanups arbitrários.
   - Documentar que `spawn_*` só pode ser chamado de dentro de `setup(ctx)` (há loop e `owner` definido).

2. **Wiring do supervisor/serviço no runtime de plugins** — análogo a `set_runtime`:
   - `plugins/context.set_runtime(...)` (hoje `:34`, recebe `ws_manager`+`loop`) passa a receber também o
     `supervisor` e o `subprocess_service` (ou um objeto `runtime` agregador). Chamado no lifespan
     (`server/app.py`, junto do `_set_plugin_runtime`).

3. **Endpoints REST (admin) + WS** — `server/routes/` (`runtime.py` novo):
   - `GET /api/runtime/tasks` → `supervisor.status()` (name, owner, state, restarts, last_error).
   - `GET /api/runtime/subprocesses` → `subprocess_service` status (name, owner, pid, running, last_error).
   - WS (UI): emitir `task_state_changed` / `subprocess_state_changed` em transições (start/crash/restart/
     stop) via `plugins.context.broadcast` (consumo do front).
   - Bus de plugins (P28): emitir `task.crashed` / `subprocess.crashed` / `subprocess.restarted` **só na
     transição** (nunca dentro do crash-loop), para plugins de notificação/auditoria reagirem.
   - **RBAC:** estes endpoints são **admin-only**. No MVP ficam atrás do `auth_middleware` atual
     (`server/app.py:245`, com isenções `/api/webhook` e `/health` em `:232` — **preservar**); ganham RBAC
     real quando o plano 03 entregar papéis (`request.state.user`).

4. **Health só em memória (P30 — DECIDIDO):** o estado de tasks/subprocessos vive **apenas em memória**
   no MVP. Os endpoints de status leem direto do `supervisor`/`subprocess_service`. **Sem tabelas core**
   `runtime_tasks`/`runtime_subprocesses` e **sem migration Alembic** nesta fase. Histórico persistente de
   crashes, se um dia for necessário, é tratado pelo plano 07 (auditoria), reaproveitando a infra dele.

### Frontend (Preact, sem build)
- Tela admin "Runtime" no `GearMenu` (ou aba dentro de `/plugins`): lista de tasks + subprocessos com
  estado, restarts, último erro; live via WS. Usar classes `wa-*` (modo escuro — regra do CLAUDE.md).
- Componente novo: `web/static/js/components/RuntimePanel.js`; rota SPA em `app.js`.

### Arquivos
- Editar: `plugins/context.py` (`spawn_task`/`spawn_subprocess`/`set_runtime` estendido), `server/app.py`
  (wiring), criar `server/routes/runtime.py` (endpoints), `web/static/js/components/RuntimePanel.js` +
  rota em `app.js`. **Sem migration Alembic** (P30 — health só em memória).

### Critério de pronto
- `ctx.spawn_task` e `ctx.spawn_subprocess` funcionam de dentro de um `setup` de plugin e são parados no
  disable (sem órfãos).
- `GET /api/runtime/tasks` e `/subprocesses` retornam o estado real; a tela Runtime atualiza ao vivo.

---

## Fase 6 — Provider de teste valida (i)+(ii) ponta a ponta

**Estado WF1:** `nao_feito`. **O que FALTA:** o plugin de validação + os testes.

**Objetivo:** provar a fundação **antes** de encostar no caso difícil (GOWA/subprocesso), exatamente
como o doc 02 §3.4.8 recomenda ("validar (i)+(ii) num provider barato sem subprocesso").

### Passos

1. **Plugin de exemplo** `assets/plugin_examples/runtime_probe/` (bundlado, `enabled=0`):
   - `plugin.yaml` com `entry.lifecycle: lifecycle`.
   - `lifecycle.py`: no `setup`, registra `ctx.on_unload(...)` (loga "teardown ok") e
     `ctx.spawn_task("heartbeat", make_loop, policy=PERMANENT)` — um loop que faz `broadcast("runtime_probe_tick", {...})` a cada N s.
   - (Sem subprocesso — valida exatamente (i)+(ii).)
   - Tela `config: true` opcional mostrando os ticks (valida o broadcast).

2. **Roteiro de validação manual** (anotar no plano de testes):
   - Enable → `setup` roda, task aparece em `/api/runtime/tasks` como `running`, ticks chegam no WS.
   - Forçar exceção na task → supervisor relança com backoff (status mostra `restart_count++`).
   - Disable → `teardown` roda (log "teardown ok"), task some do supervisor (`stop_owner`), sem órfãos.
   - Shutdown do app → teardown aguardado (medível pelo atraso intencional no `on_unload`).

3. **Teste automatizado** — `tests/test_endpoints.py` (ou novo `tests/test_runtime.py`):
   - Cobrir `GET /api/runtime/tasks` / `/subprocesses` (com supervisor mockado/real em memória).
   - Teste de unidade do `TaskSupervisor`: policy `PERMANENT` relança; `TEMPORARY` não; rate-limit vira
     `crashed`; `stop_all` aguarda.
   - Teste de unidade do `ManagedProcess`: stale-kill mata PID-file vivo; stop mata o grupo. (Pode usar
     um `sleep`/script trivial como subprocesso de teste em vez do GOWA.)

### Critério de pronto
- O `runtime_probe` passa pelo roteiro manual sem órfãos e sem cortar teardown.
- Os testes de unidade do supervisor e do ManagedProcess passam no CI local
  (`python tests/test_endpoints.py`).

---

## Retrofit P62/P67 — isolamento do code-in-DB sobre o `SubprocessService` (Onda 2, plano 06)

> **Esta seção não é uma fase deste plano** — é o **consumidor downstream** mais importante da Fase 4, e
> substitui a antiga "Pergunta em aberto #8" (que dizia "ADIADO"). Documentada aqui porque é a dependência
> cruzada 06⇄09 e a razão de a Fase 4 ser a maior alavanca.

**Estado atual (WF1):** o instalador code-in-DB **já existe e roda in-process** —
`agent/ai_tool_installer.py:77` (`exec_module`) executa o Python persistido no banco com **todos os
privilégios** (DB, FS, network, chave LLM), documentado como SECURITY DEBT em `:18`. **Mitigado por padrão**
pelo kill-switch `ai_tools_code_enabled` (default OFF, env `WHATSBOT_AI_TOOLS_CODE`) e pela tool nascendo
`enabled=False` (gate humano P63). O "checklist P0 admin-only" do relatório §6 está **OBSOLETO** (a
mitigação shipou depois).

**O retrofit (P67, Lote 3 — não mais ADIADO):** quando a **Fase 4** deste plano entregar o
`SubprocessService`/`ManagedProcess`, migrar o `ai_tool_installer` de `exec_module` in-process para um
**subprocesso isolado** sob o serviço, com **RLIMIT + timeout** (a decisão dia-1 do P62). Pontos:
- Reusa `SubprocessSpec`/`ManagedProcess`: o runner vira um subprocesso curto (não um daemon de longa
  duração) — pode exigir um modo "one-shot" no serviço (spawn + wait + colher saída), ou um wrapper
  específico no plano 06 que use as primitivas POSIX (`start_new_session`, `RLIMIT_*` via `resource`,
  timeout via `wait`).
- **Em aberto (WF1):** se o `SubprocessService` também abraça o `pkg_deps` (pip até ~600s no boot, hoje
  fora de supervisor) — decidir na implementação do retrofit, não neste plano.
- **Risco (WF1):** o RCE só está mitigado pelo kill-switch até o retrofit; alto se o operador ligar
  `ai_tools_code_enabled` antes do isolamento existir. O retrofit é **Onda 2** justamente por isso.

**Trabalho concreto deste plano para habilitar o retrofit:** garantir que a Fase 4 exponha primitivas
reutilizáveis (process group + pdeathsig + stop-da-árvore + readiness) e que `runtime/_proc_platform.py`
seja genérico o bastante para o plano 06 aplicar `RLIMIT`/timeout sem reescrever a camada POSIX.

---

## Pontos de integração com o código existente (resumo cravado)

> Offsets re-ancorados por `grep` no working tree atual; **aproximados** — confirmar com `grep` (não usar
> linha hardcoded na implementação, P-drift WF1).

| O que muda | Âncora (grep) | Como |
|---|---|---|
| Lifespan cria tasks hardcoded sem await no stop | `server/app.py` `asyncio.create_task(...)` (~:188-191) + `task.cancel()` (~:198) | substituir por `supervisor.register/start_all` + `await supervisor.stop_all()` |
| `set_runtime` dos plugins | `plugins/context.py` `def set_runtime` (~:34); chamada no lifespan (`_set_plugin_runtime`) | estender para injetar `supervisor` + `subprocess_service` |
| Toggle mata processo sem teardown | `plugins/restart.py` `def schedule_restart` (~:42) + `os._exit` (~:78); `server/routes/plugins.py` `schedule_restart(...)` (~:95/:106/:130) | `schedule_restart(..., on_before_exit=teardown)` |
| Emit fire-and-forget no shutdown | `plugins/events.py` `def emit` (~:129), `run_coroutine_threadsafe` (~:172) | shutdown passa a aguardar `lifecycle.run_teardown`, não depende mais só de `emit("app.shutdown")` (~app.py:195) |
| GOWA `Popen`/`terminate` cru | `gowa/manager.py` `Popen` (~:122), `stop()` (~:137-149), watchdog (~:169) | delegar ao `SubprocessService` (process group + die-with-parent + stale-kill + readiness) |
| Limpeza de órfão externa | `linux_start.sh` (`pkill -f bin/gowa`) | stale-kill nativo no boot torna o `pkill` redundante (manter como rede de segurança no MVP; Linux/Docker — P29) |
| Loader só importa, sem gancho | `plugins/loader.py` `_load_plugin_module` (~:188), `LoadedPlugin` (~:33) | capturar `setup`/`teardown` de `entry.lifecycle` em `LoadedPlugin` |
| Eventos do supervisor/subprocesso ausentes do bus | `plugins/events.py` `KNOWN_EVENTS` (~:39) | adicionar `task.crashed`, `subprocess.crashed`, `subprocess.restarted` (P28) |
| Code-in-DB in-process (consumidor do retrofit) | `agent/ai_tool_installer.py` `exec_module` (~:77), SECURITY DEBT (~:18) | **fora deste plano** — migrar para subprocesso isolado quando a Fase 4 entregar o serviço (retrofit P62/P67, Onda 2) |

---

## Migrations (P30 / P82)

- **Este plano NÃO cria nenhuma migration Alembic** — health de tasks/subprocessos é **só em memória**
  (P30). Nada de `runtime_tasks`/`runtime_subprocesses` no MVP.
- **Se um dia precisar** (não no MVP, p.ex. histórico persistente de crashes via plano 07): aplicar a regra
  **P82 — encadeamento linear**: `down_revision` = head real no momento de implementar (hoje
  `0008_plugin_installed_deps`); número = próximo livre (≥ 0009). **Nunca** reusar 0006/0007/0008 como slot
  novo (os slots 0007=AGNO e 0008=pkg_deps **já foram consumidos**) — ramificar a cadeia **quebra o boot**.
  Se este plano algum dia criar 2+ migrations, a 2ª encadeia na 1ª.

---

## Dependências de outros planos

- **NÃO depende de nenhum outro plano para começar.** Este é o plano **fundacional** (Onda 1): deve ser
  executado **antes** das fases de provider-plugin do plano 02 e antes do retrofit P62 (plano 06).
- **Habilita (dependências de saída):**
  - [`02-plano-canais-e-providers.md`](02-plano-canais-e-providers.md) — a extração do GOWA para
    provider-plugin (fase de subprocesso do plano 02) **depende deste plano concluído** (capacidades
    i/ii/iii). Também habilita Telegram/IMAP como plugin de polling (capacidade ii).
  - [`06-plano-motor-multiagente.md`](06-plano-motor-multiagente.md) — o **retrofit P62/P67** (isolar o
    `ai_tool_installer` code-in-DB) **depende da Fase 4** deste plano. Onda 2. Ver §"Retrofit P62/P67".
- **Cruza com:** [`03-plano-rbac-usuarios.md`](03-plano-rbac-usuarios.md) — os endpoints `/api/runtime/*` e
  a tela Runtime devem ser **admin-only**; no MVP ficam atrás do `auth_middleware` atual (preservar as
  isenções `/api/webhook` e `/health` em `server/app.py:232`) e ganham RBAC quando o plano 03 entregar
  papéis. [`07-plano-auditoria.md`](07-plano-auditoria.md) — health fica só em memória no MVP (P30); se um
  dia precisar de histórico persistente de crashes, reaproveitar a trilha de auditoria do plano 07 (⏸️
  adiado).

---

## Dependências novas (pip / JS)

- **Nenhuma.** Tudo usa stdlib: `subprocess`, `os`, `signal`, `ctypes` (para `PR_SET_PDEATHSIG` no Linux),
  `resource` (para `RLIMIT` no retrofit do plano 06), `asyncio`, `threading`.
- **Windows / Job Object / `pywin32`:** **fora do escopo** (P29 — só Linux/Docker). Nenhuma dependência
  Windows-only é adicionada. Se um dia voltarmos ao EXE Windows, reavaliar `pywin32` vs `ctypes` então.

---

## Perguntas em aberto

> **Todas as decisões de design deste plano estão FECHADAS** (P22/P25/P26/P27/P28/P29/P30/P31 no Lote 2 e
> P62/P67 no Lote 3). As entradas abaixo ficam como registro histórico — **não re-litigar**.

### 1. Disable de plugin: restart-do-processo vs hot-unload em runtime?
- ✅ **DECIDIDO (2026-06-19): (A) restart-do-processo, com teardown aguardado antes do `os._exit`** (P22 /
  P25). Hot-unload (B) fica como evolução futura — o registry por `owner` (fases 3-4) já deixa metade do
  caminho pronto.

### 2. Onde mora o supervisor e o serviço de subprocesso: `server/` ou um novo pacote `runtime/`?
- ✅ **DECIDIDO (2026-06-19): (B) novo pacote `runtime/`** (P26) — `runtime/supervisor.py`,
  `runtime/subprocess_service.py`, `runtime/_proc_platform.py`. Atualizar a seção "Arquitetura" do CLAUDE.md
  ao implementar.

### 3. `stop_event` por-task no supervisor vs o `state.stop_event` global atual?
- ✅ **DECIDIDO (2026-06-19): (A) `task.cancel()` nativo** (P27) como mecanismo único; `state.stop_event`
  global mantido **só por compat** das 4 corrotinas legadas. Já refletido na Fase 3.

### 4. Eventos do bus de plugin para o supervisor (`task.crashed`, etc.)?
- ✅ **DECIDIDO (2026-06-19): (B) emitir no bus** (P28) os eventos `task.crashed` / `subprocess.crashed` /
  `subprocess.restarted`, adicionados a `KNOWN_EVENTS`. Emitir **só na transição**. Já refletido nas
  fases 3-5.

### 5. Die-with-parent no Windows: Job Object via `ctypes` ou `pywin32`?
- ✅ **DECIDIDO (2026-06-19): ADIADO — Windows fora do escopo** (P29). Só Linux/Docker, die-with-parent via
  `PR_SET_PDEATHSIG`. O stale-kill no boot cobre o pior caso enquanto isso.

### 6. Persistir health de tasks/subprocessos em tabelas core (fase 5) já no MVP?
- ✅ **DECIDIDO (2026-06-19): (A) só memória no MVP** (P30) — endpoints leem do supervisor/serviço, **sem**
  tabelas e **sem** migration Alembic. Já refletido na Fase 5.

### 7. Timeout de teardown por plugin no shutdown/disable?
- ✅ **DECIDIDO (2026-06-19): (A) timeout fixo (~10s)** por plugin (P31), com `os._exit` como rede de
  segurança final. Já refletido nas fases 1-2.

### 8. Reusar o serviço de subprocesso para o `tool_runner` do plano 06?
- ✅ **DECIDIDO (Lote 3, 2026-06-19): SIM — é um RETROFIT, não mais ADIADO** (P67). O `tool_runner`
  code-in-DB **já existe in-process** (`agent/ai_tool_installer.py`); quando a Fase 4 entregar o
  `SubprocessService`, migrar o instalador para subprocesso isolado (RLIMIT + timeout). É a **Onda 2** e a
  dependência cruzada 06⇄09. Ver §"Retrofit P62/P67". *(A redação anterior deste plano marcava como
  "⏸️ ADIADO — decidir depois"; isso está OBSOLETO.)*
