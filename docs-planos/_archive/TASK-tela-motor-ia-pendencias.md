# TASK — Concluir a tela "Engine de IA" (Motor de IA / config-in-DB)

> **STATUS: ✅ CONCLUÍDA (2026-06-20)** — as 3 pendências foram implementadas (só frontend +
> reuso de `GET/PUT /api/config`). Resumo das mudanças:
> - **P1 (criar agente):** botão "+ Novo agente" + campo `agent_key` (slug validado) em modo criação
>   no `AgentForm` — `web/static/js/components/ai/AgentsManager.js`.
> - **P2 (restart UX):** overlay "Servidor reiniciando…" + polling em `/health` + `location.reload()`
>   quando o worker volta — `web/static/js/components/ai/AgentEngine.js`.
> - **P3 (status da flag):** badge "Ativo/Desligado" + botão "Ativar/Desativar motor" (toggle de
>   `ai_engine_enabled` via `PUT /api/config`) — `web/static/js/components/ai/AgentEngine.js`.
> - **BÔNUS (causa-raiz do stale-module):** o mount `/static` (e os de plugin) não enviavam
>   `Cache-Control`, então o browser cacheava ES modules heuristicamente e servia versões velhas —
>   é a origem do `SyntaxError ... does not provide an export named X` (InfoIcon) **e** de mudanças
>   novas não aparecerem (ex.: o botão "+ Novo agente" sumido). Fix: classe `NoCacheStaticFiles`
>   (`Cache-Control: no-cache`) em `server/app.py` → revalida sempre, com 304 via ETag. Browsers já
>   com o módulo em cache heurístico precisam de **um** hard refresh (Ctrl+Shift+R); depois disso as
>   atualizações passam a ser automáticas.

> **Origem:** teste manual em `whatsbot-dev.teste.techify.run` (2026-06-20). O operador relatou
> dois problemas na aba **Engine de IA** (menu da engrenagem → tela `/ai`): (1) o botão **"Reiniciar
> worker"** "não fez nada"; (2) **não há opção de criar novos agentes** — só dá pra editar o "Agente padrão".
>
> **Contexto maior:** ver [06-plano-motor-multiagente.md](06-plano-motor-multiagente.md). Esta task é o
> **acabamento da UI da Fase F2/F4** (CRUD de agentes pela tela), não greenfield. O backend, repos e
> tabelas já existem e funcionam.

---

## Estado atual (o que JÁ funciona)

O backend e a maior parte da UI estão prontos:

- **Backend CRUD completo** em [server/routes/ai_engine.py](../server/routes/ai_engine.py):
  agentes (`GET/PUT` + history/rollback), prompts, variáveis, tools code-in-DB, e
  `POST /api/ai/restart` (linha 255, gated por permissão `agent.manage`).
- **Repos** (`db/repositories/agent_repo.py` etc.) com `save()` upsert + snapshot de versão. **Criar
  um agente é só um `PUT /api/ai/agents/{agent_key}` com uma key nova** — não falta endpoint.
- **Frontend**: as abas **Prompts**, **Variáveis** e **Tools** têm botão "+ Novo …" e CRUD completo.
  A aba **Agentes** tem listagem + **Editar** + **Histórico/Reverter**.
- **Pipeline**: `agent/agent_factory.build_for_contact` lê o agente do DB por mensagem quando a flag
  `ai_engine_enabled` está ligada.

---

## Pendência 1 — Não dá pra criar agente novo pela UI (bug de UX, prioridade ALTA)

**Sintoma:** a aba Agentes só mostra a lista com "Editar"/"Histórico". Não há "+ Novo agente".

**Causa raiz (frontend, [web/static/js/components/ai/AgentsManager.js](../web/static/js/components/ai/AgentsManager.js)):**
- O componente só tem estado `editing` — **não existe** estado `creating` nem botão para abri-lo
  (compare com `PromptsEditor`/`VariablesEditor`/`ToolsEditor`, que têm "+ Novo …").
- O `AgentForm` assume agente existente: o título é hardcoded `"Editar agente <agent_key>"` (linha ~140)
  e o `submit()` chama `onSave(agent.agent_key, …)` (linha ~121) — **não há campo para digitar a `agent_key`
  de um agente novo**.

**O que fazer (só frontend — backend já aceita):**
1. Adicionar botão **"+ Novo agente"** no topo da aba Agentes que faz `setEditing({})` (objeto vazio =
   modo criação) ou um estado `creating` dedicado.
2. No `AgentForm`, quando for criação (sem `agent.agent_key`):
   - Renderizar um `<input>` para a **`agent_key`** (slug `^[a-z][a-z0-9_]{0,31}$`, validar/normalizar;
     mostrar erro se vazio/duplicada — checar contra a lista já carregada).
   - Trocar o título para "Novo agente" e o `submit()` para usar a key digitada em vez de `agent.agent_key`.
   - Bloquear/ocultar o seletor de versão ("v1") no modo criação.
3. Após salvar, `load()` recarrega a lista (já implementado em `handleSave`).

**Critério de aceite:** operador clica "+ Novo agente", digita key + nome + prompt + modelo, salva, e o
agente aparece na lista como "Inativo" (ou "Ativo" conforme o checkbox). Editar/Histórico funcionam nele.

---

## Pendência 2 — "Reiniciar worker" parece não fazer nada (UX/feedback, prioridade MÉDIA)

**Diagnóstico:** o fluxo **funciona** — não é bug de lógica. Ao clicar, o front chama `restartAi()`
→ `POST /api/ai/restart` → `schedule_restart()` que faz `os._exit(0)` após 1,5s. Em produção (Coolify,
`CMD python main.py` **sem** `--reload` — [main.py:89](../main.py#L89)), o `os._exit` derruba o
container e o orquestrador o relança. Os sintomas vistos no console do teste confirmam que o restart
ocorreu: **`GET /api/balance 502 (Bad Gateway)`** + WebSocket caindo + o `SyntaxError` de módulo
estale enquanto os assets recarregavam.

Ou seja: o problema é de **percepção/feedback**, não de funcionalidade. O usuário vê "Reinício
agendado…" e logo depois a página silenciosamente quebra (502 / WS off / módulo estale) em vez de um
estado claro de "reiniciando, reconectando…".

**O que fazer (UX):**
1. Após `restartAi()` ok, entrar num estado **"Servidor reiniciando…"** visível e persistente
   (overlay/banner), não só uma linha de texto efêmera.
2. **Auto-recuperar**: fazer polling em `GET /health` (ou reusar o reconnect do WebSocket) e, quando
   o backend voltar, **recarregar a página** (`location.reload()`) para pegar os assets novos — isso
   também elimina o `SyntaxError` de módulo estale que aparece em todo restart.
3. (Opcional) Suprimir/segurar o toast de erro do `GET /api/balance` enquanto estiver no estado de
   restart, para não assustar o operador com o 502 esperado.

**Critério de aceite:** clicar "Reiniciar worker" mostra um estado claro de reinício, a página se
reconecta sozinha quando o worker volta e recarrega sem erro de módulo no console.

---

## Pendência 3 — A tela não mostra se o motor (`ai_engine_enabled`) está ligado (clareza, prioridade MÉDIA)

**Sintoma:** todo o config-in-DB só vale quando `ai_engine_enabled = True` (default **OFF**). Hoje a
tela só tem um aviso em texto ("As mudanças passam a valer quando o motor de IA estiver ativado") e o
próprio código admite (comentário em `AgentEngine.js:3`): *"there's no status endpoint for the
ai_engine_enabled flag"*. O operador pode editar agentes o dia todo achando que estão valendo quando o
motor está desligado.

**O que fazer:**
1. Expor a flag `ai_engine_enabled` na API (incluir em `GET /api/config` se ainda não estiver, ou um
   campo num endpoint de status do motor).
2. No topo de `AgentEngine.js`, mostrar um **badge de status**: "Motor de IA: **Ativo**/**Desligado**".
3. (Opcional) Toggle para ligar/desligar a flag direto da tela (persistir via `PUT /api/config` +
   `handler.update(ai_engine_enabled=…)`), evitando depender de env/config manual.

**Critério de aceite:** ao abrir a tela, o operador vê imediatamente se o motor está ativo ou não.

---

## Fora de escopo (já coberto pelo plano 06, não fazer aqui)

- Isolamento do runner de tools code-in-DB (subprocess/RLIMIT) — F3 retrofit, Onda 2.
- Multi-agente por inbox e handoff/routing executável (F4/F5) — dependem dos planos 01/02.
- Endurecimento das colunas JSON para `JSONB` no Postgres — endurecimento opcional.

## Resumo dos arquivos a tocar

| Pendência | Arquivo | Mudança |
|---|---|---|
| 1 (criar agente) | `web/static/js/components/ai/AgentsManager.js` | botão "+ Novo agente" + campo `agent_key` no `AgentForm` em modo criação |
| 2 (restart UX) | `web/static/js/components/ai/AgentEngine.js` | estado de reinício + polling `/health` + `location.reload()` |
| 3 (status da flag) | `server/routes/...` + `web/static/js/components/ai/AgentEngine.js` | expor `ai_engine_enabled` + badge (e toggle opcional) |

**Nenhuma mudança de schema ou de backend CRUD é necessária para a Pendência 1.**
