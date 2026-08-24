# Plano 137 — O `retornos` para de perder conversa por lentidão própria (concorrência nível 1, in-process)

> **Status:** ✅ EXECUTADO (2026-08-21) · **Escopo:** médio (1 plugin; **zero mudança no core**, **zero migration**) · **Entregue como:** `retornos` **1.20.0**, instalada em local, **não publicada**
> **Origem:** incidente em produção — 12 conversas relatadas pelo operador não receberam o retorno das 09:00 de 19/08. **Método:** consulta somente-leitura ao banco de produção pelo cofre de credenciais (a identificação da credencial fica fora deste documento — repositório público) + leitura do código real, com `arquivo:linha` verificados.
> **O quê/porquê:** as conversas não foram avaliadas — foram **canceladas pela janela de tolerância** antes de as condições rodarem, porque o ciclo é **serial** e não conseguiu drenar a fila dentro dos 15 minutos de `grace`. O plano corrige três coisas na ordem em que elas mordem: a tolerância passa a medir **indisponibilidade** em vez de relógio de parede, o lock passa a ter heartbeat de job inteiro, e o despacho passa a ser **concorrente com teto configurável**. Continua tudo dentro do plugin, ligável/desligável pela tela de Plugins.
>
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0 — Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 ✅ (2026-08-21) | **Nível 1**: concorrência **in-process**, dentro do plugin. Nada de worker em processo separado. | Não se usa `ctx.spawn_subprocess`. O modelo de plugin (toggle, `ctx.spawn_task`, `stop_owner`) fica intacto. Ver §7 a regra que mantém o nível 2 aberto de graça. |
| D2 ✅ (2026-08-21) | **Retorno atrasado DEVE sair.** 09:05 em vez de 09:00 é aceitável; ser cancelado não é. | O `grace` deixa de ser "quanto atraso tolero" e vira **"quanto tempo o verificador esteve morto"**. Sem isso, todo pico continua matando a cauda — só com números maiores. |
| D3 ✅ (2026-08-21) | **Não apertar as condições** (`Hora de disparo entre 09:00 e 09:30`) nesta rodada. | Com a vazão atual, janela com teto troca "sai atrasado" por "expira em silêncio", que é pior. Só depois de F4 medido. Ver P4. |
| D4 ✅ (2026-08-21) | O teto de concorrência é **setting do plugin**, default conservador. | O gargalo real é externo (rate limit do proxy Techify), não o servidor. O botão tem de ser ajustável em produção sem deploy. |
| D5 ✅ (2026-08-21) | Os hops bloqueantes do plugin saem do `ThreadPoolExecutor` **default**. | O pool default é por onde as rotas do painel chamam os repos (`asyncio.to_thread`). Saturá-lo trava o atendente — e esse é o risco maior, não o event loop. |
| D6 ✅ (2026-08-21) | **Zero mudança no core e zero migration.** | O carimbo do heartbeat vai numa config key do próprio plugin (`plugin.retornos.*`), que já é namespace dele. Manifesto continua `">=1.0,<2.0"`. |
| D7 ✅ (2026-08-21) | Publicação como **1.20.0** (MINOR). | Muda comportamento e acrescenta settings, mas não quebra schema nem contrato. Bump em 3 lugares + rebuild + **instalar no local antes de publicar**. |
| D8 ✅ (2026-08-21) | O heartbeat de lock (F3) entra **antes** da concorrência (F4). | Sem ele, F4 introduz **mensagem duplicada para o cliente**. É a única dependência dura do plano. |

**Princípio fixo:** o plugin manda mensagem para cliente real. Entre "atrasar um follow-up" e "mandar duas vezes / mandar para quem acabou de responder", perde-se o follow-up. Toda guarda nova é fail-safe nessa direção.

---

## 1 — Resumo executivo

Às 09:00 de 19/08, ~230 controles venceram no mesmo segundo (toda a régua usa `Hora de disparo >= 09:00`). O verificador rodou **sem parar** de 09:00:35 a 09:14:10 e despachou 103. Aos 09:15:10 o ciclo seguinte reclamou 50 controles cuja espera já passava de 15 min e **cancelou os 50 de uma vez**; aos 09:16:10, mais 30. Total: **80 conversas perdidas sem avaliar uma única condição** (`tentativas_retorno = 0` em todas).

A causa não é capacidade de máquina nem queda de servidor. São três defeitos que se somam:

1. **A cadência degrada sob carga** — `sleep(60)` roda *depois* do trabalho, então o período é `duração + 60s`. Com 50 controles × ~5,7s, o "verificador por minuto" vira um verificador a cada ~5 minutos.
2. **A tolerância pune atraso que o próprio plugin causou** — `grace` compara `now - next_at` e não distingue "o processo esteve morto" de "o processo esteve vivo e ocupado".
3. **Não há concorrência nenhuma** — um turno de IA por vez, bloqueando a thread do ciclo em `fut.result(timeout=120)`.

Nada disso exige core, processo separado ou migration. O plano corrige na ordem 2 → lock → 3, porque só o item 2 já para a perda de conversa, e o item 3 sem o heartbeat de lock produz mensagem duplicada.

---

## 2 — Como funciona hoje (mapa verificado)

### 2.1 O laço e o ciclo

| Passo | Arquivo:linha | O que faz |
|---|---|---|
| Task supervisionada | [lifecycle.py:53](../whatsbot-pro-plugins/plugins/retornos/src/lifecycle.py#L53) `ctx.spawn_task("scheduler", …, PERMANENT)` | aparece como `retornos:scheduler` em `GET /api/runtime/tasks` |
| Registro do loop | [lifecycle.py:31](../whatsbot-pro-plugins/plugins/retornos/src/lifecycle.py#L31) `actions.set_loop(asyncio.get_running_loop())` | guarda o loop **principal do FastAPI** |
| Ciclo em thread | [lifecycle.py:34](../whatsbot-pro-plugins/plugins/retornos/src/lifecycle.py#L34) `await asyncio.to_thread(dispatcher.run_cycle)` | ocupa uma thread do pool **default** |
| ⚠️ Cadência | [lifecycle.py:50](../whatsbot-pro-plugins/plugins/retornos/src/lifecycle.py#L50) `await asyncio.sleep(CHECK_INTERVAL_SECONDS)` | **depois** do trabalho ⇒ período = duração + 60s |
| Recovery de lock | [dispatcher.py:324](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L324) | solta `processing=1` parado há > `STALE_LOCK_SECONDS` ([:32](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L32) = 300s) |
| Claim atômico | [dispatcher.py:329](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L329) → [repo.py:548-557](../whatsbot-pro-plugins/plugins/retornos/src/repo.py#L548-L557) | `UPDATE … RETURNING`, `ORDER BY next_at ASC LIMIT :lim` |
| ⚠️ Laço serial | [dispatcher.py:341](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L341) `for ctrl in pendentes:` | um controle por vez, do começo ao fim |

### 2.2 O ponto exato onde as 80 conversas morreram

```python
# dispatcher.py:186-196
grace  = max(0, int(getattr(cfg, "grace_minutes", 15) or 0)) * 60.0
atraso = now - float(ctrl.get("next_at") or now)
if grace > 0 and atraso > grace:
    repo.set_status(cid, repo.STATUS_CANCELLED, ...)
```

⚠️ **Esse teste roda ANTES do `rules.avaliar`** ([dispatcher.py:229](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L229)). É por isso que as condições nunca foram executadas e `tentativas_retorno` ficou em `0` — não adianta procurar defeito na árvore de regras.

Prova no banco (`plugin_retornos_log`, 19/08):

| conversa | quando | evento | `data` |
|---|---|---|---|
| 15919 | 09:15:10 | `cancelled` | `{"motivo": "grace_window", "atraso_min": 15.2}` |
| 9814 | 09:16:10 | `cancelled` | `{"motivo": "grace_window", "atraso_min": 16.2}` |

### 2.3 O que bloqueia a thread do ciclo

```
process_controle                     ← dispatcher.py:172, laço serial
└─ actions.execute                   ← actions.py:417
   └─ _run_on_loop(_ia_coro(...))    ← actions.py:473
      └─ fut.result(timeout=120.0)   ← actions.py:82  ⚠️ BLOQUEIA aqui
```

E o que o `_ia_coro` ([actions.py:317](../whatsbot-pro-plugins/plugins/retornos/src/actions.py#L317)) faz antes de liberar:

1. `await ah.aprocess_message(...)` — turno completo do AGNO (raciocínio + tool calling), **no loop principal do FastAPI**;
2. divide a resposta (`parse_split_reply`);
3. por parte: `_send_text` → `save_assistant_message` → `broadcast`, todos via `asyncio.to_thread` (pool **default**).

Mais o `post_private_note` ([actions.py:113](../whatsbot-pro-plugins/plugins/retornos/src/actions.py#L113)) que roda **antes** do turno, com escrita e broadcast.

### 2.4 O heartbeat de lock que existe — e o que ele não cobre

```python
# dispatcher.py:100-118 — _pausar()
```

Ele renova `processing_since` a cada 30s ([:34](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L34)) **só durante a pausa entre mensagens**. O docstring ([:104](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L104)) já explica o perigo que ele previne:

> *"o MESMO retorno dispararia de novo enquanto o primeiro disparo ainda está no meio das mensagens (mensagem duplicada para o cliente)"*

⚠️ **Hoje o resto do job não tem heartbeat**, e isso é inofensivo apenas porque claim e processamento acontecem em segundos. Numa fila com espera por vaga, um job pode ficar `processing=1` além dos 300s, ser solto pelo `recover_stale_locks` ([repo.py:537-545](../whatsbot-pro-plugins/plugins/retornos/src/repo.py#L537-L545) — que **não** mexe no `next_at`, ainda no passado) e ser **reclamado e disparado de novo**.

### 2.5 Medições de produção (base de dimensionamento)

| Métrica | Valor | Fonte |
|---|---|---|
| Tempo por **slot** (controle reclamado, já com retries intercalados) | **~5,7s** | ciclo B de 19/08: 50 slots em 283s |
| Tempo por **disparo** | mediana 6,4s · média 7,4s · máx 26,8s | gaps entre `dispatched` consecutivos |
| Intervalos entre ciclos | 66,9s · 65,6s · 60,0s | confirma `duração + 60s` |
| Slots gastos em `retry` (condição não bateu) | **27%** (41 de 151) | ciclos A–C de 19/08 |
| Conversas entrando na régua/dia | 432 (17/08) · **1016** (18/08) · 880 (19/08) · 566 (20/08) | evento `armed` |
| Teto atual dentro do `grace` | `grace×60 ÷ 5,7` ⇒ **~315 conversas** com `grace=30` | derivado |

### 2.6 Settings em produção (lidas em 2026-08-21)

| Chave | Valor | Observação |
|---|---|---|
| `grace_minutes` | **30** | era 15 no incidente; subido por Thiago em 21/08 07:42 (`audit_log`) |
| `max_per_cycle` | **300** | era o default 50 no incidente — os lotes de 50 e 30 cancelados o comprovam |
| `max_attempts_per_retorno` | 60 | **não participou** do incidente |
| `retorno_deadline_minutes` | 4320 | **não participou** |
| `delay_between_messages_seconds` | 2 | ver falso positivo FP2 |

---

## 3 — Inventário das mudanças

| # | Item | Arquivo:linha | O que falta | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|---|
| I1 | Cadência fixa | [lifecycle.py:50](../whatsbot-pro-plugins/plugins/retornos/src/lifecycle.py#L50) | `sleep(60)` depois do trabalho | `sleep(max(0, tick − elapsed))` com `time.monotonic()` | baixo | S |
| I2 | Carimbo do ciclo | [dispatcher.py:315-355](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L315-L355) | não existe | grava `plugin.retornos._last_cycle_ts` via `config_repo` ao fim de cada ciclo — **sem migration** (§4.1) | baixo | S |
| I3 | **Grace mede indisponibilidade** | [dispatcher.py:186-196](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L186-L196) | compara relógio de parede | `atraso_efetivo` = tempo em que o verificador esteve **parado** desde `next_at` (§4.2) | **alto** | M |
| I4 | **Heartbeat de job inteiro** | [dispatcher.py:100-118](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L100-L118) (só cobre a pausa) | job sem heartbeat fora da pausa | heartbeat próprio do job, ativo do claim ao desfecho | **alto** | M |
| I5 | **Concorrência com teto** | [dispatcher.py:341](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L341) `for ctrl in pendentes:` | laço serial | `run_cycle` vira async; semáforo sobre o despacho; `_run_on_loop` some (§4.3) | **alto** | L |
| I6 | Executor privado | [actions.py:73-82](../whatsbot-pro-plugins/plugins/retornos/src/actions.py#L73-L82) + hops de [_ia_coro](../whatsbot-pro-plugins/plugins/retornos/src/actions.py#L317) | usa o pool **default**, compartilhado com o painel | `ThreadPoolExecutor` próprio do plugin, dimensionado com o teto | médio | M |
| I7 | Exclusão por conversa | [dispatcher.py:329](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L329) | não existe | nunca dois jobs concorrentes do mesmo `conversation_id` (§5, risco R4) | baixo | S |
| I8 | Re-checagem antes do envio | [actions.py:452-473](../whatsbot-pro-plugins/plugins/retornos/src/actions.py#L452-L473) | `ai_allowed` usa o `conv` do claim | recarrega via `evalctx.load_target` ([evalctx.py:40](../whatsbot-pro-plugins/plugins/retornos/src/evalctx.py#L40)) imediatamente antes | médio | S |
| I9 | Backoff em recusa do provedor | [dispatcher.py:271-273](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L271-L273) | 429 cai em `_reagendar_ou_expirar(motivo="envio_falhou")` e **queima tentativa** | recusa de rate limit não conta tentativa e recua o teto | médio | M |
| I10 | Settings novas | [settings.py](../whatsbot-pro-plugins/plugins/retornos/src/settings.py) | não existem | `max_concurrent_dispatches`, `grace_counts_only_downtime` | baixo | S |
| I11 | Observabilidade do cancelamento | [lifecycle.py:38-46](../whatsbot-pro-plugins/plugins/retornos/src/lifecycle.py#L38-L46) (tick) + tela | grace-cancel é mudo | contador no tick + linha no Monitor | baixo | M |
| I12 | Testes | [tests/python/](../whatsbot-pro-plugins/plugins/retornos/tests/python/) (2 arquivos hoje) | nada cobre ciclo/grace/concorrência | suíte nova (F7) | baixo | L |
| I13 | Publicação | `plugin.yaml:3`, `retornos.json`, `README.md` | versão 1.19.0 | bump **1.20.0** + rebuild + instalar local | baixo | S |

### 3.1 Falsos positivos descartados

| # | Suspeita | Por que **não** é |
|---|---|---|
| FP1 | "`max_per_cycle=50` foi a causa" | Foi o *formato* do sintoma (lotes de 50 e 30), não a causa. Com I3, um teto baixo deixa de ser letal: a cauda **espera o ciclo seguinte** em vez de morrer. **Não subir mais** — 300 já está acima do necessário. |
| FP2 | "A pausa de 10s entre mensagens agravou" | `if i and espera` ([dispatcher.py:250](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L250)) pula a **primeira** mensagem, e os 4 retornos têm **1 mensagem cada** (verificado no banco). A pausa nunca foi aplicada — em 10s ou em 1s. **Não mexer.** |
| FP3 | "Queda do servidor / deploy" | O log mostra despacho **contínuo** de 09:00:35 a 09:14:10, com 103 `dispatched`. O verificador nunca parou. |
| FP4 | "Estourou `max_attempts_per_retorno` / `retorno_deadline_minutes`" | `tentativas_retorno = 0` nas 12 conversas. Nenhum dos dois tetos foi tocado. |
| FP5 | "As condições estão erradas / têm buraco entre 15:30 e 16:00" | Irrelevante para este incidente: o `grace` corta **antes** do `rules.avaliar`. Continua valendo como observação separada, fora deste plano. |
| FP6 | "`proximo_instante` achatar os grupos OU é bug a corrigir" | Ele agenda **cedo demais**, o que é seguro por construção (reavalia com dados frescos — o próprio docstring diz "é só um **hint**"). O que produz o envio às 14:00 é a condição `>= 09:00` **não ter teto**, e isso é configuração do operador — travado em D3. |
| FP7 | "O heartbeat do `_pausar` está errado" | Está **certo** para o que cobre. Ele só não cobre o job inteiro — que é exatamente o que I4 acrescenta. Não reescrever, estender. |

---

## 4 — Mudanças de infraestrutura (dentro do plugin)

### 4.1 O carimbo do heartbeat não precisa de migration

O valor é **singleton** (um por instância), não por controle. Vai numa config key do namespace do plugin:

```
plugin.retornos._last_cycle_ts   ← escrita ao fim de todo ciclo
```

⚠️ Chave **não declarada** em `Settings` de propósito: `load_settings` ([dispatcher.py:43-60](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L43-L60)) itera `Settings.model_fields`, então uma chave a mais é simplesmente ignorada, e `GET /api/plugins/retornos/settings` (que serve `model_json_schema()`) não a expõe no formulário. É estado interno, não configuração.

### 4.2 A nova semântica do `grace`

| Situação | Hoje | Depois |
|---|---|---|
| Verificador vivo o tempo todo desde `next_at` | cancela se `now − next_at > grace` | **nunca cancela** — despacha atrasado (D2) |
| Verificador parado (deploy, crash, container recriado) | cancela | cancela se a **indisponibilidade** > `grace` |
| `grace_minutes = 0` | dispara sempre | dispara sempre (inalterado) |

Fórmula: `indisponibilidade = max(0, agora − max(next_at, _last_cycle_ts))`. Se o ciclo anterior rodou depois do `next_at`, o verificador estava vivo e a dívida é nossa.

⚠️ **Fail-safe na direção de despachar**: `_last_cycle_ts` ausente (1º boot, config apagada) ⇒ trata como "estava vivo" ⇒ **não cancela**. Perder um follow-up é pior que mandá-lo atrasado (D2).

### 4.3 Onde a concorrência entra

Duas formas possíveis; o plano recomenda a **(a)**:

| | (a) `run_cycle` async no loop principal **(recomendada)** | (b) `ThreadPoolExecutor` dentro da thread do ciclo |
|---|---|---|
| `_run_on_loop` | **some** — o turno já é `await` direto | continua, N threads bloqueadas em `fut.result` |
| Threads ocupadas | menos que hoje | N |
| Trabalho bloqueante | `run_in_executor(pool_privado, …)` (I6) | dentro das próprias threads |
| Risco | fatias de CPU do turno no loop do painel (ver P2) | contenção de pool, igual hoje mas multiplicada |

⚠️ **Não criar um segundo event loop.** O `broadcast` do core tem loop e `ws_manager` injetados no startup apontando para o loop principal; um segundo loop obrigaria travessia disciplinada por `run_coroutine_threadsafe` em todo ponto. Fica no loop principal + executor privado só para os hops bloqueantes.

---

## 5 — Fases e paralelização

```
WAVE 0   F0(caracterização) 🔴
             │ (barreira: nada muda antes da rede de segurança existir)
WAVE 1   F1 · F2 · F6                        ← 🟢 independentes entre si
             │ (F2 já para a perda de conversa — pode ir a produção sozinha)
WAVE 2   F3 🔴  [bloqueia F4]
             │
WAVE 3   F4 🔴 ──┬── F5 🟢 (arquivo diferente)
                 │
WAVE 4   F7(testes) → F8(publicação) 🔴
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | caracterização | 🔴 | baixo | suíte nova verde descrevendo o comportamento atual |
| 1 | **F1** | cadência | 🟢 | baixo | intervalo entre ciclos = 60s, não `duração+60s` |
| 1 | **F2** | grace por indisponibilidade | 🟢 | **alto** | backlog fundo com verificador vivo ⇒ **zero** `grace_window` |
| 1 | **F6** | exclusão por conversa | 🟢 | baixo | dois controles da mesma conversa nunca concorrem |
| 2 | **F3** | heartbeat de job | 🔴 `[bloqueia F4]` | **alto** | job longo nunca é solto pelo `recover_stale_locks` |
| 3 | **F4** | concorrência + executor | 🔴 `[depende de F3]` | **alto** | N em voo; vazão ≥ 4× a serial |
| 3 | **F5** | re-checagem pré-envio | 🟢 `[arquivo: actions.py]` | médio | cliente que respondeu no meio da fila não recebe o follow-up |
| 4 | **F7** | testes | 🔴 | baixo | `test_plugins.py retornos` verde |
| 4 | **F8** | publicação | 🔴 | baixo | 1.20.0 instalada local, zip conferido |

**Disciplina do repo:** verde a cada fase · caracterização ANTES de mexer em fluxo crítico · um refactor por commit · nunca avançar com teste vermelho não-explicado.

---

### Fase 0 — Caracterização (rede de segurança)

**Objetivo:** travar em teste o comportamento atual, inclusive o defeituoso, para que F2/F3/F4 mostrem exatamente o que mudaram.

**Itens**
- `[paralelo]` Teste do **caminho de retry**: condição falsa ⇒ `tentativas_retorno + 1`, `next_at` = `proximo_instante`, status segue `active` ([dispatcher.py:135-170](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L135-L170)). **Não pode mudar em nenhuma fase.**
- `[paralelo]` Teste dos **cancelamentos legítimos**: configuração inativa ([:176](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L176)), conversa removida ([:203](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L203)), grupo sem `apply_to_groups` ([:210](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L210)). **Não podem mudar.**
- `[paralelo]` Teste do **teto de segurança**: `max_attempts_per_retorno` e `retorno_deadline_minutes` ⇒ `expired`. **Não pode mudar.**
- `[sequencial]` **Teste-âncora do incidente**: fila maior que a capacidade de drenagem, verificador vivo ⇒ hoje a cauda é `cancelled` com `motivo=grace_window`. Este é o único que **inverte** em F2 (vira `dispatched`) — deixar comentado no arquivo que a inversão é esperada e em qual fase.

**Pronto quando:** `python3 scripts/test_plugins.py --python-only retornos` verde, com o teste-âncora **passando** ao descrever o comportamento atual (ainda defeituoso).

#### Status de execução — Fase 0
**Estado:** ✅ Concluída (2026-08-21)
- **O que foi feito:** Suíte nova [tests/python/test_ciclo_e_grace.py](../whatsbot-pro-plugins/plugins/retornos/tests/python/test_ciclo_e_grace.py) (10 testes) + a bancada compartilhada [tests/python/conftest.py](../whatsbot-pro-plugins/plugins/retornos/tests/python/conftest.py).
- **Como foi feito / decisões:** A bancada entrega TUDO pela fixture `env` (módulos do plugin + `armar`/`settings`/`sem_enviar`/`vivo_ha`) porque um `conftest.py` não é importável entre módulos de teste sem transformar o diretório em pacote — e o nome do pacote (`python`) colidiria entre plugins. Cada teste zera as 5 tabelas do plugin e as settings: o schema é recriado uma vez por SESSÃO e `run_cycle` processa tudo que estiver vencido, então sem a limpeza um teste contamina o seguinte pela fila.
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `test_plugins.py --python-only retornos` → **47 passed** (37 antigos + 10 novos), com o teste-âncora descrevendo o comportamento AINDA defeituoso.

---

### Fase 1 — Cadência fixa

**Objetivo:** o relógio do verificador para de desacelerar quando a fila está funda.

**Itens**
- `[sequencial]` [lifecycle.py:50](../whatsbot-pro-plugins/plugins/retornos/src/lifecycle.py#L50): medir com `time.monotonic()` antes do ciclo e dormir `max(0, CHECK_INTERVAL_SECONDS − elapsed)`.
- `[sequencial]` Ciclo que estourar o intervalo **não** encadeia sem pausa — piso pequeno (ex.: 1s) para não virar laço quente com a fila cheia.

**Pronto quando:** com fila artificial que faça o ciclo durar ~90s, os `armed`/`dispatched` do log mostram início de ciclo a cada ~90s (piso), e com fila vazia a cada 60s exatos — nunca `duração + 60`.

#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-08-21)
- **O que foi feito:** [lifecycle.py](../whatsbot-pro-plugins/plugins/retornos/src/lifecycle.py): `time.monotonic()` antes do ciclo, `sleep(max(MIN_SLEEP_SECONDS, CHECK_INTERVAL_SECONDS − gasto))`; constante nova `MIN_SLEEP_SECONDS = 1.0`. Testes em [test_cadencia.py](../whatsbot-pro-plugins/plugins/retornos/tests/python/test_cadencia.py) (4).
- **Como foi feito / decisões:** Os testes rodam o laço REAL (capturado pelo `spawn_task` de um `ctx` falso) com as constantes encolhidas para décimos de segundo e `asyncio.sleep` instrumentado — em vez de testar só a aritmética, que não provaria que o laço a chama com o `gasto` certo.
- **Problemas / pendências:** Nenhuma.
- **Verificação:** 4 testes: intervalo desconta a duração · ciclo rápido dorme quase o período inteiro · ciclo mais longo que o período cai no piso · ciclo que levanta não derruba o laço nem para o relógio.

---

### Fase 2 — O `grace` passa a medir indisponibilidade

**Objetivo:** parar a perda de conversa. É a fase que resolve o incidente e **pode ir a produção sozinha**.

**Itens**
- `[sequencial]` I2 — ao fim de `run_cycle` ([dispatcher.py:315-355](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L315-L355)), gravar `plugin.retornos._last_cycle_ts`. Escrever **também** quando o ciclo não reclamou nada (fila vazia é prova de vida) e quando o `claim_due` falhou ([:330-332](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L330-L332))? **Não** — falha de banco não é prova de vida útil; gravar só no caminho de sucesso.
- `[sequencial]` I3 — [dispatcher.py:186-196](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L186-L196): trocar `atraso` por `indisponibilidade` (§4.2). Manter a mensagem de `last_error` e o `data` do log **informando os dois números** (`atraso_min` e `downtime_min`), para o Monitor continuar legível e a comparação ficar auditável.
- `[sequencial]` I10 — setting `grace_counts_only_downtime` (default `True`) como escotilha de reversão sem deploy.
- `[paralelo]` Atualizar a `description` do `grace_minutes` em [settings.py](../whatsbot-pro-plugins/plugins/retornos/src/settings.py) — o texto atual ("Se um agendamento vencer e o servidor só voltar a rodar depois desse tempo") já descreve a semântica NOVA; hoje o código não faz isso. Alinhar texto e comportamento.

**Pronto quando:**
- O teste-âncora de F0 **inverte**: fila funda + verificador vivo ⇒ `dispatched`, zero `grace_window`.
- Teste novo: verificador parado por mais de `grace` ⇒ **continua** cancelando (a proteção de outage não regrediu).
- Teste novo: `_last_cycle_ts` ausente ⇒ não cancela (fail-safe de §4.2).

#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-08-21)
- **O que foi feito:** [dispatcher.py](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py): `LAST_CYCLE_KEY`, `ler_ultimo_ciclo()`/`marcar_ciclo()`, `process_controle(..., last_cycle_ts=)` e o novo corte de grace; [settings.py](../whatsbot-pro-plugins/plugins/retornos/src/settings.py): `grace_counts_only_downtime` + texto do `grace_minutes` alinhado ao comportamento. O teste-âncora foi INVERTIDO e ganhou 7 vizinhos.
- **Como foi feito / decisões:** O carimbo é gravado só no FIM do ciclo e só no caminho de sucesso. Verificador DESLIGADO pelo interruptor mestre não carimba — religar depois de dias tem de se comportar como voltar de uma parada, senão a fila represada sairia toda de uma vez. A conta usa o `now` CONGELADO do ciclo (já era assim), e é isso que impede um ciclo longo de inflar a indisponibilidade e reintroduzir o bug em escala maior.
- **Problemas / pendências:** **Limite aceito e documentado no código**: só o carimbo mais recente é guardado, então uma parada longa cuja fila não caiba num único ciclo cancela o primeiro lote e ENTREGA (atrasado) o resto. Os dois desfechos são defensáveis e o segundo é o lado seguro; resolver exigiria persistir a janela da parada, e não vale o estado extra.
- **Verificação:** Âncora invertido (`dispatched`, zero `grace_window`) + parada de 2h ainda cancela + vencimento DENTRO da parada conta do vencimento + sem carimbo não cancela + escotilha restaura o relógio de parede + 4 testes do carimbo. **55 passed**.

---

### Fase 3 — Heartbeat de job inteiro `[bloqueia F4]`

**Objetivo:** tornar impossível que um job em andamento seja solto pelo `recover_stale_locks` e disparado duas vezes. **É a dependência dura de F4** (D8).

**Itens**
- `[sequencial]` I4 — heartbeat ativo do **claim ao desfecho**, não só durante `_pausar` ([dispatcher.py:100-118](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L100-L118)). Reaproveitar o `repo.update_controle(id, processing_since=…)` que o `_pausar` já usa ([:114](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L114)) — não inventar mecanismo novo (FP7).
- `[sequencial]` Revisar a relação entre `STALE_LOCK_SECONDS` ([:32](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L32) = 300s), `LOCK_HEARTBEAT_SECONDS` ([:34](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L34) = 30s) e o `timeout=120` do `_run_on_loop` ([actions.py:73](../whatsbot-pro-plugins/plugins/retornos/src/actions.py#L73)): o heartbeat tem de ser folgadamente menor que o stale, **e** o stale folgadamente maior que o pior job possível.
- `[sequencial]` Garantir que o heartbeat morre com o job — job que termina, falha ou é cancelado não pode deixar tarefa de heartbeat órfã renovando lock de controle já liberado.

**Pronto quando:**
- Teste: job que dura mais que `STALE_LOCK_SECONDS` **não** é solto pelo `recover_stale_locks` e **não** dispara duas vezes.
- Teste: processo morto no meio ⇒ o lock **ainda é** recuperado depois do stale (a função original não regrediu).
- Teste: nenhum heartbeat sobrevive ao fim do job.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída (2026-08-21)
- **O que foi feito:** [repo.py](../whatsbot-pro-plugins/plugins/retornos/src/repo.py) `touch_locks()`; [dispatcher.py](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py) `_LoteHeartbeat` + `_executar_controle`/`_despachar` (extraídos do laço de `run_cycle`). Suíte [test_lock_heartbeat.py](../whatsbot-pro-plugins/plugins/retornos/tests/python/test_lock_heartbeat.py) (8).
- **Como foi feito / decisões:** **Desvio do plano:** UMA thread para o lote inteiro, não uma por controle — 300 pendentes seriam 300 threads. Cada job tira o próprio id num `finally`, e o `UPDATE` do heartbeat encolhe junto com a fila. `touch_locks` carrega `AND processing = 1`, então um carimbo atrasado nunca alcança um lock já solto. A soma do sumário saiu de dentro do job e passou a ser feita na thread do ciclo — com N threads, `resumo[k] += 1` perderia contagem em silêncio.
- **Problemas / pendências:** Nenhuma. `_pausar` foi mantido intacto (FP7): sua renovação virou redundante, não errada.
- **Verificação:** Âncora (job 2,5× mais longo que o stale não é solto) **com contra-teste** ao lado — sem o par, um heartbeat quebrado passaria despercebido, porque `recover` também devolve 0 quando nada está travado. Mais: processo morto ainda é recuperado · nenhuma thread sobrevive ao lote, nem com job que explode · fila vazia não abre thread. **64 passed**.

---

### Fase 4 — Concorrência com teto configurável `[depende de F3]`

**Objetivo:** o tempo do ciclo deixa de ser proporcional ao tamanho da fila.

**Itens**
- `[sequencial]` I5 — `run_cycle` ([dispatcher.py:315](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L315)) vira async na forma (a) de §4.3; o `for` serial ([:341](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L341)) vira despacho concorrente com `asyncio.Semaphore(N)`. O `await asyncio.to_thread(dispatcher.run_cycle)` de [lifecycle.py:34](../whatsbot-pro-plugins/plugins/retornos/src/lifecycle.py#L34) passa a `await` direto.
- `[sequencial]` **O semáforo cobre só o DESPACHO.** A avaliação de condições (`rules.avaliar`, [:229](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L229)) é milissegundos e não usa LLM — não pode disputar vaga com quem vai enviar. Isso devolve os 27% de slots que hoje viram `retry`.
- `[sequencial]` I6 — `ThreadPoolExecutor` privado do plugin para os hops bloqueantes (D5); `_run_on_loop` ([actions.py:73-82](../whatsbot-pro-plugins/plugins/retornos/src/actions.py#L73-L82)) é **removido** — o turno vira `await` direto.
- `[sequencial]` I10 — setting `max_concurrent_dispatches`, default **4** (ver P1), clamp `1..64`. `1` reproduz exatamente o comportamento serial de hoje — é a escotilha de reversão.
- `[sequencial]` I9 — recusa por rate limit do provedor **não conta tentativa**: hoje qualquer falha cai em `_reagendar_ou_expirar(motivo="envio_falhou")` ([dispatcher.py:271-273](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py#L271-L273)), incrementando `tentativas_retorno` — uma tempestade de 429 empurraria conversas saudáveis para `expired`.
- `[sequencial]` Confirmar o dimensionamento do pool do Postgres para `N` jobs em voo (cada job faz nota privada + histórico + usage + `save_assistant_message` + updates de controle).

**Pronto quando:**
- Fila de 200 controles drena em ≤ ¼ do tempo serial, com `max_concurrent_dispatches = 4`.
- `max_concurrent_dispatches = 1` ⇒ comportamento byte-equivalente ao de hoje (todos os testes de F0 verdes).
- Nenhuma conversa recebe mensagem duplicada (heartbeat de F3 sob carga).
- Teste: 429 simulado ⇒ `tentativas_retorno` **não** incrementa e o teto recua.

#### Status de execução — Fase 4
**Estado:** ✅ Concluída (2026-08-21)
- **O que foi feito:** [dispatcher.py](../whatsbot-pro-plugins/plugins/retornos/src/dispatcher.py): executor privado (`_pool`/`shutdown_pool`/`teto_de_concorrencia`, `MAX_CONCURRENT_DISPATCHES = 12`), `_despachar` concorrente, `recusa_por_limite` + `contar_tentativa`/`atraso_minimo` em `_reagendar_ou_expirar` (I9), contador `cancelled_grace` (I11); [settings.py](../whatsbot-pro-plugins/plugins/retornos/src/settings.py) `max_concurrent_dispatches`; [lifecycle.py](../whatsbot-pro-plugins/plugins/retornos/src/lifecycle.py) desmonta o pool no teardown e publica o contador no tick; [static/retornos.js](../whatsbot-pro-plugins/plugins/retornos/src/static/retornos.js) selo âmbar. Suíte [test_concorrencia.py](../whatsbot-pro-plugins/plugins/retornos/tests/python/test_concorrencia.py) (14).
- **Como foi feito / decisões:** **Desvio do plano — forma (b) de §4.3, não (a).** `run_cycle` continua SÍNCRONO e o fan-out é um `ThreadPoolExecutor` privado; `_run_on_loop` fica de pé (R10 some). Motivo: atende D5 (os hops bloqueantes saem do pool default) com uma fração da mudança, o que é a restrição explícita do pedido — e o tamanho do pool já É o semáforo, então nem `asyncio.Semaphore` entra. Não há risco novo de CPU no loop principal (P2 fica como estava). **Segundo desvio:** o teto é 12, não 64. O plano pedia para confirmar o dimensionamento do pool do Postgres; confirmado que o engine do core usa os defaults do SQLAlchemy (`pool_size=5` + `max_overflow=10` = 15 conexões), acima de ~12 a vazão não sobe — vira espera de pool aparecendo como falha aleatória. Um clamp que permite um valor que quebra o sistema é armadilha.
- **Problemas / pendências:** Nenhuma. P1 resolvido como recomendado: default **4**.
- **Verificação:** Sobreposição REAL medida (não só "terminou mais rápido") · teto respeitado · `N=1` roda na PRÓPRIA thread do ciclo, sem executor no caminho · jobs só em threads `retornos-dispatch` (D5) · trocar o teto recria o executor sem restart · sumário não perde contagem em 12 jobs · job que explode não derruba os outros · 429 não queima tentativa **com contra-teste** (falha comum continua queimando) e o PRAZO ainda termina uma recusa permanente. **78 passed**.

---

### Fase 5 — Re-checagem imediatamente antes do envio `[arquivo: actions.py]`

**Objetivo:** o follow-up não sai para quem respondeu, resolveu ou foi assumido depois do claim.

**Itens**
- `[sequencial]` I8 — em [actions.py:452-473](../whatsbot-pro-plugins/plugins/retornos/src/actions.py#L452-L473), recarregar a conversa por `evalctx.load_target` ([evalctx.py:40](../whatsbot-pro-plugins/plugins/retornos/src/evalctx.py#L40)) e reavaliar `ai_allowed` ([actions.py:289](../whatsbot-pro-plugins/plugins/retornos/src/actions.py#L289)) **imediatamente antes** do turno — não com o `conv` carregado no claim.
- `[sequencial]` Mesmo tratamento para o teste de janela de 24h ([actions.py:445-451](../whatsbot-pro-plugins/plugins/retornos/src/actions.py#L445-L451)), que também depende de dado que envelhece na fila.
- `[sequencial]` Bloqueado na re-checagem ⇒ o caminho já existente de nota privada, **sem** contar disparo. Precedente do core: `messaging_service._ai_may_speak_now` ([app/services/messaging_service.py:220](app/services/messaging_service.py#L220)) reconsulta pelo mesmo motivo (plano 96). **Não importar do core** — replicar o padrão, mantendo o plugin carregável em core anterior.

**Pronto quando:** teste em que a conversa muda de estado (cliente responde / atendente assume / IA desligada) **entre** claim e envio ⇒ nada é enviado, nota privada gravada, `disparos_enviados` intacto.

#### Status de execução — Fase 5
**Estado:** ✅ Concluída (2026-08-21)
- **O que foi feito:** [actions.py](../whatsbot-pro-plugins/plugins/retornos/src/actions.py): o ramo `ia_responde_agora` relê a conversa por `evalctx.load_target` imediatamente antes de `ai_allowed`. Suíte [test_recheck_pre_envio.py](../whatsbot-pro-plugins/plugins/retornos/tests/python/test_recheck_pre_envio.py) (5).
- **Como foi feito / decisões:** Fail-**CLOSED** quando a releitura não vem: entre atrasar um follow-up e mandá-lo para quem acabou de responder, perde-se o follow-up (princípio fixo do plano). Sem nota privada nesse caminho — a falha é de leitura, não do atendimento. **Desvio do plano:** o bloqueio CONTINUA contando disparo, ao contrário do que a fase pedia. O caminho de nota privada já existia devolvendo `ok: True`, e não contar faria `sucessos == 0` ⇒ `envio_falhou` ⇒ retry a cada ciclo, gravando uma nota nova toda vez até a conversa ser liberada. A semântica atual (nota + avança) é a correta; o que a F5 muda é a FONTE do dado, não a contagem. **Nada foi importado do core** — o padrão de `messaging_service._ai_may_speak_now` é replicado, para o plugin seguir carregando num core anterior.
- **Problemas / pendências:** A janela de 24h não precisou de mudança: `janela_aberta` consulta o último inbound na hora, então já era fresca por construção — registrado em comentário no código para ninguém "consertar" de novo. Duas fixtures antigas quebraram legitimamente (usavam conversa só em memória) e passaram a criar conversa REAL no banco, com a razão escrita no teste.
- **Verificação:** IA desligada / atendente assumido DEPOIS do claim ⇒ nota privada, IA não acionada · leitura indisponível ⇒ nada enviado e nenhuma nota · a conversa relida é a do agendamento. **83 passed**.

---

### Fase 6 — Exclusão por conversa

**Objetivo:** dois controles da mesma conversa nunca rodam ao mesmo tempo.

**Itens**
- `[sequencial]` I7 — no despacho, garantir unicidade por `conversation_id`; o segundo controle da mesma conversa fica para o ciclo seguinte (**não** é cancelado nem conta tentativa).
- `[sequencial]` Hoje o cenário não ocorre (a 2ª configuração, "teste", está inativa em produção), mas duas configurações ativas produzem dois controles para a mesma conversa — e, com F4, dois turnos de IA intercalados no mesmo chat.

**Pronto quando:** teste com duas configurações ativas sobre a mesma conversa ⇒ os turnos são serializados, nunca intercalados.

#### Status de execução — Fase 6
**Estado:** ✅ Concluída (2026-08-21)
- **O que foi feito:** Nenhum código. Suíte [test_um_controle_por_conversa.py](../whatsbot-pro-plugins/plugins/retornos/tests/python/test_um_controle_por_conversa.py) (3) fixando a invariante.
- **Como foi feito / decisões:** **Desvio do plano — o cenário é impossível.** `plugin_retornos_controle` tem o índice ÚNICO `plugin_retornos_controle_conv` sobre `conversation_id` desde a migration `001`, e `upsert_controle` faz `ON CONFLICT (conversation_id) DO UPDATE`: uma segunda configuração SUBSTITUI o controle da conversa, não acrescenta um segundo. O conjunto de exclusão seria código morto — pior, código morto com cara de garantia. Em vez dele, os testes fixam a invariante de que a concorrência depende, e caem se alguém trocar o índice por `(conversation_id, configuracao_id)` para permitir réguas paralelas.
- **Problemas / pendências:** Se algum dia réguas paralelas por conversa virarem requisito, a exclusão volta ao radar — e agora com teste que avisa ANTES de o cliente receber duas mensagens sobrepostas.
- **Verificação:** Segunda configuração substitui o controle · INSERT direto de um segundo controle é recusado pelo banco (`IntegrityError`) · lote concorrente de 10 nunca traz conversa repetida. **86 passed**.

---

### Fase 7 — Observabilidade e testes

**Objetivo:** um lote morrer nunca mais depender de alguém ir olhar no banco.

**Itens**
- `[paralelo]` I11 — o tick ([lifecycle.py:38-46](../whatsbot-pro-plugins/plugins/retornos/src/lifecycle.py#L38-L46)) passa a carregar o desfecho por motivo; o Monitor mostra quantos foram cancelados por `grace_window` e por indisponibilidade.
- `[paralelo]` I12 — suíte cobrindo: cadência, grace por indisponibilidade, heartbeat sob carga, concorrência com `N=1` e `N>1`, re-checagem, exclusão por conversa, 429.
- `[paralelo]` Aba **Eventos** já existe (1.10.0) e já lista `cancelled` — verificar que o `motivo` novo aparece legível, **modo escuro incluído**.

**Pronto quando:** `python3 scripts/test_plugins.py --python-only retornos` verde e o Monitor mostra o contador com um lote forçado.

#### Status de execução — Fase 7
**Estado:** ✅ Concluída (2026-08-21)
- **O que foi feito:** Observabilidade entregue junto da F4 (contador `cancelled_grace` no sumário, WARNING dedicado no log, campo no `retornos_tick` e selo âmbar ao lado de "Verificador ativo"). Testes: 6 arquivos novos, **57 testes** somados aos 33 que já existiam.
- **Como foi feito / decisões:** O selo usa `bg-amber-100 text-amber-700`, coberto pelo fallback `html.dark` do `custom.css`, ao lado do selo verde existente que usa o mesmo padrão.
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `python3 scripts/test_plugins.py retornos` → **90 passed** (Python) + **33 pass / 0 fail** (JS `node --test`).

---

### Fase 8 — Publicação

**Objetivo:** 1.20.0 no repositório de plugins do Pro, sem divergir do que roda.

**Itens**
- `[sequencial]` Bump em **4** lugares (o plano dizia 3): `plugin.yaml:3`, `retornos.json` (`"version"`), **`catalog.json`** (é o `build_plugins.py` que reclama) e `README.md`, + nota de versão no `description` do manifesto e do JSON (padrão do plugin).
- `[sequencial]` ⚠️ **Instalar no local ANTES de publicar** — a cópia viva é `storages/plugins/retornos/`, e é ela que o operador testa. Commit/zip não muda o que roda.
- `[sequencial]` ⚠️ **Conferir o remoto antes de concluir paridade**: `git fetch` + comparar; o repositório de plugins do Pro é um segundo remoto e pode ter versão publicada no meio do trabalho. Comparar **conteúdo**, nunca só o número.
- `[sequencial]` `python3 scripts/build_plugins.py retornos` + `--check`. ⚠️ `--check` pode acusar "outdated" falso por umask (664 vs 644) — rebuildar para "consertar" é o caminho destrutivo.

**Pronto quando:** `--check` limpo, plugin instalado em local, versão conferida contra a tabela `plugins` de produção.

#### Status de execução — Fase 8
**Estado:** ✅ Concluída (2026-08-21)
- **O que foi feito:** Bump para **1.20.0** em `plugin.yaml`, `retornos.json`, `catalog.json` e nota de versão; README atualizado (seção nova "O ciclo do verificador", linha da IA em "Regras travadas", texto da pausa que dizia "o ciclo é serial", e o comando de teste, que apontava para `assets/plugin_examples/retornos/` — caminho que não existe mais). ZIP gerado e **instalado em local**.
- **Como foi feito / decisões:** **O plano dizia 3 lugares de versão; são 4** — `catalog.json` também, e é o builder que reclama. Registrado aqui para o próximo plano não repetir a conta. O build exigiu parquear temporariamente `plugins/pagamentos/` (WIP não versionado de outra frente, fora do catálogo, que trava a validação de cobertura); devolvido e conferido por checksum (55 arquivos, hash idêntico).
- **Problemas / pendências:** **Nada foi commitado nem publicado** — o repositório de plugins fica com as mudanças em árvore, aguardando decisão. A cópia instalada estava em **1.18.0**, duas versões atrás; foi substituída pelo ZIP da 1.20.0 (backup da 1.18.0 no scratchpad da sessão).
- **Verificação:** `build_plugins.py retornos --check` → `current` (sem falso "outdated": build feito com `umask 022`). ZIP com 29 arquivos, nenhum teste/cache/db dentro. Instalada = idêntica à fonte. Remoto (`origin/main`) e **produção** conferidos ANTES do bump: ambos em 1.19.0, sem versão publicada no meio do trabalho.

---

## 6 — Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| **R1 — lock velho** | Job esperando vaga cruza `STALE_LOCK_SECONDS`, é solto por [repo.py:537-545](../whatsbot-pro-plugins/plugins/retornos/src/repo.py#L537-L545) (que **não** mexe no `next_at`) e é reclamado de novo ⇒ **cliente recebe duas vezes** | F3 **antes** de F4 (D8), com teste explícito de job mais longo que o stale |
| **R2 — dado velho** | Condições avaliadas no claim, envio minutos depois ⇒ follow-up para quem acabou de responder | F5 recarrega e reavalia imediatamente antes do envio |
| **R3 — rate limit do provedor** | 429 do proxy Techify vira `envio_falhou` ⇒ queima `tentativas_retorno` ⇒ `expired`. Rate limit vira **perda de conversa**, não lentidão | I9: recusa de rate limit não conta tentativa + recuo do teto; `N` default conservador |
| **R4 — dois controles, mesma conversa** | Duas configurações ativas ⇒ turnos de IA intercalados no mesmo chat | F6 |
| **R5 — painel lento** | `N` turnos concorrentes disputando o `ThreadPoolExecutor` default, por onde as rotas chamam os repos | D5 / I6: executor privado; `N` configurável; medir P2 antes de subir |
| **R6 — pool do Postgres** | `N` jobs × várias escritas cada ⇒ timeout de pool, que aparece como falha aleatória | dimensionar o pool junto com `N` (F4) |
| **R7 — restart do plugin** | Toggle chama `os._exit`; jobs em voo morrem com `processing=1` | comportamento já existente e correto: o `recover_stale_locks` os devolve à fila. F3 não pode quebrar isso — teste dedicado |
| **R8 — regressão de semântica do grace** | Instalação que **quer** o corte por relógio de parede | `grace_counts_only_downtime` (default `True`) reverte sem deploy |
| **R9 — segundo event loop** | `broadcast` aponta para o loop principal (injetado no startup) | §4.3: **não** criar segundo loop; executor privado só para hops bloqueantes |
| **R10 — `_run_on_loop` removido** | Outro ponto do plugin pode depender dele | grep antes de remover: hoje o único chamador é [actions.py:473](../whatsbot-pro-plugins/plugins/retornos/src/actions.py#L473) — **confirmar na execução** |

---

## 7 — A regra de custo zero que mantém o nível 2 aberto

D1 fecha a porta do worker em processo separado **por ora**, não para sempre. Custa nada hoje e caro depois:

1. **Estado do trabalho vive no banco, com claim atômico.** Já é verdade ([repo.py:548-557](../whatsbot-pro-plugins/plugins/retornos/src/repo.py#L548-L557)) — é essa propriedade que permitiria N workers dividirem a fila sem redesenhar lock.
2. **Nenhum estado de plugin em memória de processo.** ⚠️ F4 introduz o primeiro candidato a violar isso: o **semáforo** e o conjunto de exclusão por conversa (F6) são de processo. Aceitável no nível 1 — mas devem ser *hints de coordenação local*, nunca a única garantia de correção. A garantia de correção continua sendo o lock no banco.
3. **Não assumir que `broadcast` é em processo.** Hoje é; um worker separado precisaria de `LISTEN/NOTIFY` ou equivalente. É o único custo escondido real do nível 2.

**Gatilhos para reconsiderar o nível 2:** mais de uma réplica processando a régua · volume da régua dominando o tráfego do painel · exigir que deploy de automação não derrube o atendimento.

---

## 8 — Perguntas em aberto

**P1 — Qual o default de `max_concurrent_dispatches`?**
✅ **DECIDIDO na execução (2026-08-21): (a) 4**, com teto de **12** (não 64). O engine do core usa
os defaults do SQLAlchemy — `pool_size=5` + `max_overflow=10` = 15 conexões —, então acima de ~12
a disputa deixa de ser pelo provedor e passa a ser pelo banco, e a vazão não sobe.
Contexto: o teto real é externo (rate limit do proxy Techify), não o servidor. (a) `4` — conservador, já 4× a vazão atual, praticamente sem risco de 429. (b) `16` — atende os 5.000/dia projetados (~2.000 no pico das 09:00 drenando em ~12,5 min).
**Recomendação:** nascer em **(a) 4**, medir o limite real do proxy, subir pela setting em produção. Subir é um clique; um lote inteiro em 429 vira perda de conversa (R3).

**P2 — Quanto de cada turno é CPU no loop principal?**
✅ **PREJUDICADA pela forma escolhida na F4.** A concorrência ficou num `ThreadPoolExecutor`
privado com `run_cycle` síncrono (forma (b) de §4.3), então o turno continua chegando ao loop
principal exatamente como antes, um por vez por job — o número não mudou de natureza e não é ele
que limita `N`. A medição volta a importar se algum dia a forma (a) for adotada.
Contexto: é o número que decide se `N` alto é seguro. ~50ms de CPU num turno de 6s ⇒ 16 concorrentes ocupam ~13% do loop (invisível). ~300ms ⇒ ~80% (painel arrasta). A diferença entre os dois cenários costuma ser **uma tool fazendo banco síncrono sem `to_thread`**.
**Recomendação:** medir antes de passar de `N=4`; se der alto, o conserto é a tool, não o `N`.

**P3 — O `grace` deve continuar cancelando em outage longo?**
✅ **DECIDIDO (2026-08-21):** sim. A proteção original ("não mandar mensagem de ontem hoje") continua valendo para **queda real**. O que muda é só a medida — indisponibilidade, não relógio de parede (§4.2).

**P4 — Apertar as condições para `Hora de disparo entre 09:00 e 09:30`?**
⏸️ **ADIADO** — reavaliar depois de F4 medido em produção (D3).
Contexto: hoje `>= 09:00` não tem teto, então continua verdadeira às 14:00 e às 23:59 — é isso que produz o envio fora de hora, não o atraso. Com a vazão atual, pôr teto troca "sai atrasado" por "expira em silêncio".
**Recomendação:** quando entrar, a folga deveria ser **um campo do retorno** ("validade do disparo"), não a mesma janela escrita três vezes em três grupos — que vai divergir.

**P5 — O que fazer com os 80 controles cancelados de 19/08 e os 30 de 20/08?**
⏸️ **ADIADO** — decisão do operador.
Contexto: estão em `status=cancelled` e não voltam sozinhos. (a) deixar como está (o cliente já pode ter respondido desde então). (b) re-armar os que seguem elegíveis.
**Recomendação:** (a). São conversas de 2 dias atrás; um follow-up "você sumiu" agora é pior que nenhum.

---

## 9 — Checklist de verificação

- [ ] `python3 scripts/test_plugins.py --python-only retornos` verde
- [ ] Suíte do core verde no Postgres (`WHATSBOT_TEST_DB_URL`) — o plugin não muda o core, mas F5 encosta em contrato de conversa
- [ ] `max_concurrent_dispatches = 1` ⇒ todos os testes de F0 verdes (reversão real)
- [ ] `grace_counts_only_downtime = False` ⇒ comportamento antigo restaurado
- [ ] Fila de 200 sob `N>1` ⇒ **zero** mensagem duplicada (R1)
- [ ] Job mais longo que `STALE_LOCK_SECONDS` não é reclamado duas vezes
- [ ] Processo morto no meio ⇒ lock ainda recuperado depois do stale (R7)
- [ ] Conversa que muda de estado entre claim e envio ⇒ nada enviado (R2)
- [ ] Monitor e aba Eventos legíveis no **modo escuro**
- [ ] Restart do plugin (enable/disable) não deixa heartbeat órfão nem controle preso
- [ ] Sem migration nova; `plugin_migrations` do `retornos` inalterada
- [ ] Sem segredo em URL ou log
- [ ] `build_plugins.py retornos --check` limpo (⚠️ falso "outdated" por umask)
- [ ] Plugin **instalado em local** antes de publicar
- [ ] Versão conferida contra a tabela `plugins` de produção (pode ter sido publicada no meio do trabalho)

---

## 10 — Apêndice: arquivos-chave

**Plugin — núcleo da mudança** (`../whatsbot-pro-plugins/plugins/retornos/src/`)

| Arquivo | Fases | Papel |
|---|---|---|
| `lifecycle.py` | F1, F4, F7 | laço do verificador, cadência, tick |
| `dispatcher.py` | F0, F2, F3, F4, F6 | ciclo, grace, heartbeat, laço serial |
| `actions.py` | F4, F5 | `_run_on_loop`, `_ia_coro`, `execute`, `ai_allowed` |
| `settings.py` | F2, F4 | settings novas + texto do `grace_minutes` |
| `repo.py` | F3 (leitura) | `claim_due`, `recover_stale_locks`, `update_controle` |
| `evalctx.py` | F5 (leitura) | `load_target` para a re-checagem |

**Plugin — testes e publicação**

| Arquivo | Fases |
|---|---|
| `tests/python/` (2 arquivos hoje) | F0, F7 |
| `plugin.yaml`, `retornos.json`, `README.md` | F8 |

**Core — apenas leitura, nada muda**

| Arquivo:linha | Por quê |
|---|---|
| [app/services/messaging_service.py:202](app/services/messaging_service.py#L202) `ai_may_speak` · [:220](app/services/messaging_service.py#L220) `_ai_may_speak_now` | precedente do padrão de re-checagem (plano 96) que F5 replica — **não importar** |
| [runtime/supervisor.py](runtime/supervisor.py) | `RestartPolicy.PERMANENT` da task; F1/F4 não mudam o contrato |
| [plugins/context.py:435](plugins/context.py#L435) `spawn_task` | como a task é registrada e morta no disable |
