# Plano 32 — Auto-migração do chat_id do alerta de desconexão (grupo Telegram → supergrupo)

> **Status:** ✅ IMPLEMENTADO (2026-07-06) — F1+F2+F3+F4 concluídas; ressalva: suíte de endpoints não rodou (sem pytest/Postgres de teste neste ambiente). · **Data:** 2026-07-06 · **Escopo:** pequeno (1 helper central no plugin `gowa` + persistência do novo id + higiene do estado agregado; **sem** migration nova)
>
> **Origem:** pedido do usuário — quando um grupo do Telegram usado como destino do alerta de desconexão vira **supergrupo**, o `chat_id` muda (ex.: `-100307…` → `-100XXXXXXXXXX`) e o `sendMessage` para o id antigo passa a falhar, então as notificações somem silenciosamente. **Método:** leitura do código real do plugin (`assets/plugin_examples/gowa/alerts.py` e `routes.py`) + verificação `arquivo:linha` + contrato da Bot API do Telegram (campo `parameters.migrate_to_chat_id` na resposta de erro).
>
> A solução é **reativa e sem polling**: interceptar a resposta de erro da Bot API, detectar `migrate_to_chat_id`, **reescrever o `chat_id` salvo** em `config` (`plugin.gowa.disconnect_alert_chat_id`) e **repetir a chamada** no novo id — de forma transparente aos callers. O estado da mensagem agregada (`__all__`) é higienizado para postar uma mensagem nova no destino correto.
>
> **Como usar este plano:** ao executar cada fase, preencha o bloco **"Status de execução"** dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro. Verde a cada fase; **um refactor por commit**.
>
> Legenda de estado de execução: `⬜ Não iniciada` · `🟡 Em andamento` · `✅ Concluída` · `⛔ Bloqueada`.
> Legenda de paralelização: `🟢 PODE AGRUPAR` (sem dependência) · `🔴 FAÇA SOZINHA` (sequencial/bloqueante).

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | ✅ **Detecção REATIVA via resposta de erro da Bot API** (`parameters.migrate_to_chat_id`), NÃO proativa via `getUpdates`. | O bot de alerta é **send-only** (nunca faz `getUpdates`). Adicionar polling de updates seria escopo novo e um segundo loop de rede. O campo de erro é o mecanismo canônico recomendado pelo Telegram e cobre 100% dos envios. |
| D2 | ✅ **O novo `chat_id` é PERSISTIDO em `config`** (`plugin.gowa.disconnect_alert_chat_id`), reescrevendo o valor antigo. | Auto-cura permanente (o campo na tela do plugin passa a mostrar o novo id). Sem isso, cada ciclo repetiria a falha+migração. |
| D3 | ✅ **Interceptação CENTRAL em `_tg_call`** (não em cada caller nem no `_tick`). | Um único ponto cobre `sendMessage`/`editMessageText`/`deleteMessage` uniformemente e devolve sucesso transparente. `_tick` não precisa saber de migração. |
| D4 | ✅ **`chat_id` do alerta é GLOBAL** (não por canal). | Só existe **uma** chave a reescrever (`plugin.gowa.disconnect_alert_chat_id`). O liga/desliga é por canal, mas o destino Telegram é único. |
| D5 | ✅ **Sem migration / sem mudança de schema.** Reusa a chave de `config` + a linha `__all__` da tabela existente, que se auto-cura. | Escopo pequeno, sem round-trip de Alembic nem SQL de plugin novo. |
| D6 | ✅ **Editar o plugin BUNDLED (`assets/plugin_examples/gowa/`) — a cópia versionada.** A cópia viva `storages/plugins/gowa/` (gitignored) é atualizada em paralelo para testar em dev. | Ambas precisam do mesmo patch até o próximo boot limpo. Ver §7. |

---

## 1. Resumo executivo

O alerta de desconexão do plugin `gowa` fala **direto** com a Bot API do Telegram usando um `chat_id` fixo salvo em `config`. Quando o grupo de destino é promovido a **supergrupo**, o Telegram troca o id do chat: toda chamada ao id antigo passa a retornar `ok:false` com `parameters.migrate_to_chat_id` apontando o novo id, e o alerta some sem aviso.

A correção intercepta esse erro **no ponto central `_tg_call`**: ao detectar `migrate_to_chat_id`, (a) grava o novo id na `config`, (b) reexecuta a chamada com o novo `chat_id`, devolvendo sucesso ao caller como se nada tivesse acontecido. Em `_tick`, um guarda trata o estado da mensagem agregada como "sem mensagem" quando o `chat_id` mudou — assim uma mensagem nova é postada no destino certo em vez de tentar apagar/editar uma mensagem no chat defunto. **Nenhuma migration**; o mesmo guarda cobre também a troca **manual** do `chat_id` pelo usuário.

---

## 2. Como funciona hoje (mapa) — `arquivo:linha` verificado

Todos os caminhos vivem em [assets/plugin_examples/gowa/alerts.py](../assets/plugin_examples/gowa/alerts.py) (cópia viva espelhada em `storages/plugins/gowa/alerts.py`).

### 2.1 Camada Telegram (Bot API direta)
- **`_tg_call(token, method, payload)`** — [alerts.py:215‑226](../assets/plugin_examples/gowa/alerts.py#L215): faz `POST {TELEGRAM_API}/bot{token}/{method}`, parseia JSON, loga warning quando `ok:false`, e **devolve o `data` cru**. É o único ponto de rede — todos os envios passam aqui.
- Callers de `_tg_call`, todos carregando `chat_id` no payload:
  - **`_tg_send(token, chat_id, text_html)`** — [alerts.py:229‑238](../assets/plugin_examples/gowa/alerts.py#L229) → `sendMessage`.
  - **`_tg_edit(token, chat_id, message_id, text_html)`** — [alerts.py:241‑254](../assets/plugin_examples/gowa/alerts.py#L241) → `editMessageText` (definido, mas o fluxo agregado atual usa delete+send, não edit).
  - **`_tg_delete(token, chat_id, message_id)`** — [alerts.py:257‑266](../assets/plugin_examples/gowa/alerts.py#L257) → `deleteMessage`.

### 2.2 Origem do `chat_id`
- Chave de `config`: **`plugin.gowa.disconnect_alert_chat_id`** (prefixo `_CFG = "plugin.gowa."`, [alerts.py:65](../assets/plugin_examples/gowa/alerts.py#L65)).
- Lida a cada ciclo em `_alert_config()` → `cfg["chat_id"]` — [alerts.py:100](../assets/plugin_examples/gowa/alerts.py#L100).
- Escrita pela tela do plugin: `PUT /alert-settings` grava `disconnect_alert_chat_id` — [routes.py:117](../assets/plugin_examples/gowa/routes.py#L117).
- `config_repo` **já está importado** em alerts.py — [alerts.py:46](../assets/plugin_examples/gowa/alerts.py#L46) (`from db.repositories import channel_repo, config_repo`), então persistir o novo id não exige import novo.

### 2.3 Uso do `chat_id` no loop (`_tick`) e o estado agregado
- A mensagem agregada de queda mora na linha reservada `AGGREGATE_KEY = "__all__"` — [alerts.py:144](../assets/plugin_examples/gowa/alerts.py#L144).
- No envio ([alerts.py:412‑422](../assets/plugin_examples/gowa/alerts.py#L412)):
  - `old_chat = agg.get("telegram_chat_id") or cfg["chat_id"]` — [alerts.py:414](../assets/plugin_examples/gowa/alerts.py#L414);
  - apaga a anterior (`_tg_delete`) → manda a nova (`_tg_send`) → salva `telegram_chat_id=cfg["chat_id"]`, `telegram_message_id=mid`, `down_signature=…` — [alerts.py:418‑422](../assets/plugin_examples/gowa/alerts.py#L418).
- No "tudo reconectou" ([alerts.py:388‑399](../assets/plugin_examples/gowa/alerts.py#L388)): `old_chat = agg.get("telegram_chat_id") or cfg["chat_id"]` → `_tg_delete` → limpa o estado.
- ⚠️ **Consequência da migração:** após o id mudar, `agg.telegram_chat_id` continua sendo o **antigo**. `_tg_delete(old_chat=antigo, old_mid)` cairá no mesmo erro de migração (auto-tratado pela F1, mas o `message_id` não existe no chat novo → delete falha inofensivamente). Sem um guarda, gasta-se um round de rede tentando mexer numa mensagem de um chat defunto. A F2 remove esse ruído.

### 2.4 Ponto secundário (fora do loop)
- **`POST /alert-test`** — [routes.py:140‑165](../assets/plugin_examples/gowa/routes.py#L140): usa **httpx cru** (não `_tg_call`, [routes.py:153‑160](../assets/plugin_examples/gowa/routes.py#L153)) para o botão "testar" da tela. Não passa pela interceptação central. É acionado manualmente pelo usuário e devolve o erro do Telegram na UI — baixo impacto, mas pode ganhar a mesma cortesia (F3, opcional).

### 2.5 Contrato do Telegram (verificado contra a doc da Bot API)
Quando o grupo vira supergrupo, **qualquer** método (`sendMessage`/`editMessageText`/`deleteMessage`) mirando o id antigo retorna:
```json
{ "ok": false, "error_code": 400,
  "description": "Bad Request: group chat was upgraded to a supergroup chat",
  "parameters": { "migrate_to_chat_id": -1001234567890 } }
```
O campo autoritativo é **`parameters.migrate_to_chat_id`** (não confie na string `description`). Chamar o método de novo com esse id resolve.

---

## 3. Inventário / análise

| # | Mudança | Onde (`arquivo:linha`) | Abordagem | Risco | Esforço |
|---|---------|------------------------|-----------|-------|---------|
| C1 | Interceptar migração e reexecutar | `alerts.py:215‑266` (`_tg_call` + callers) | Ao ver `parameters.migrate_to_chat_id` e `payload` com `chat_id` **igual ao antigo**: persistir novo id em `config` (via `to_thread`), reexecutar `_tg_call` uma vez com `chat_id` trocado, devolver o resultado do retry. Loga INFO. | baixo | S |
| C2 | Higiene do estado agregado na troca de chat | `alerts.py:388,406‑414` (`_tick`) | Guarda: se `agg.telegram_chat_id` existe e `!= cfg["chat_id"]`, tratar como **sem mensagem existente** (não deletar/editar o msg antigo) → posta nova no destino atual. Cobre migração **e** troca manual. | baixo | S |
| C3 (opcional) | Paridade no `/alert-test` + persistência | `routes.py:140‑165` | Rotear o teste pelo mesmo helper migração-aware (ou duplicar a checagem do campo) para que testar no id antigo também auto-migre. | baixo | S |

### 3.1 Falsos positivos descartados

| Suspeita | Por que NÃO é ponto de mudança |
|----------|-------------------------------|
| "Precisa de `getUpdates`/webhook para detectar a migração" | Falso — o bot é send-only e o Telegram entrega `migrate_to_chat_id` **na resposta de erro do próprio envio**. Polling seria um segundo loop de rede sem ganho (D1). |
| "Precisa de coluna/tabela nova" | Falso — reusa a chave `config` e a linha `__all__`; o estado agregado se auto-cura com o guarda da C2 (D5). |
| "Tem que tratar o `_tg_edit`" | O fluxo agregado atual usa **delete + send**, não `editMessageText` ([alerts.py:415‑417](../assets/plugin_examples/gowa/alerts.py#L415)). Ainda assim, interceptar em `_tg_call` (C1) cobre `_tg_edit` de graça se ele voltar a ser usado. |
| "O `chat_id` é por canal — vários ids para migrar" | Falso — é **global** (`plugin.gowa.disconnect_alert_chat_id`, [routes.py:117](../assets/plugin_examples/gowa/routes.py#L117)); só o liga/desliga é por canal (D4). |
| "Migração pode vir numa resposta `ok:true`" | Não — `migrate_to_chat_id` só aparece em `parameters` de resposta de **erro** 400. Após o primeiro retry bem-sucedido não reaparece. |

---

## 4. Fases / Roadmap

Frente única, arquivos concentrados. Dependência mínima: **C1 é a base**; C2 é independente de C1 (guarda em `_tick`) mas ambos tocam `alerts.py`, então fazem-se no **mesmo commit lógico** para evitar conflito de leitura do arquivo. C3 é opcional e independente (`routes.py`).

```
WAVE 0   F1 (C1) ─ F2 (C2)          ← mesmo arquivo (alerts.py): fazer juntas, um commit
              │  (barreira: F1+F2 verdes bloqueiam F3/F4)
WAVE 1   F3 (C3, routes.py) 🟢  ·  F4 (testes + verificação) 🟢   ← independentes entre si
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|------|------|-----------|-------|-------|---------------|
| 0 | F1 | `alerts.py` `_tg_call` (C1) | 🔴 FAÇA SOZINHA [bloqueia: F3, F4] | baixo | Envio no id antigo auto-migra e entrega no novo id (ver critério F1) |
| 0 | F2 | `alerts.py` `_tick` (C2) | 🔴 FAÇA SOZINHA (mesmo arquivo de F1) | baixo | Troca de chat_id posta mensagem nova sem tentar deletar no chat velho |
| 1 | F3 | `routes.py` `/alert-test` (C3, opcional) | 🟢 PODE AGRUPAR | baixo | Testar no id antigo migra e responde ok |
| 1 | F4 | Testes + verificação manual | 🟢 PODE AGRUPAR | baixo | Suíte verde no Postgres + validação manual |

---

### Fase F1 — Interceptação central da migração em `_tg_call` (C1) 🔴

**Objetivo:** ao receber `parameters.migrate_to_chat_id`, reescrever o `chat_id` salvo e reexecutar a chamada no novo id, de forma transparente.

**Itens:**
1. `[sequencial]` Em `_tg_call` ([alerts.py:215‑226](../assets/plugin_examples/gowa/alerts.py#L215)), após obter `data`, detectar migração: `not data.get("ok")` **e** `data.get("parameters", {}).get("migrate_to_chat_id")` presente **e** `payload.get("chat_id")` presente (só migra chamadas de chat).
2. `[sequencial]` Ao detectar:
   - `new_id = str(data["parameters"]["migrate_to_chat_id"])`;
   - persistir: `await asyncio.to_thread(config_repo.set, _CFG + "disconnect_alert_chat_id", new_id)` (`config_repo` já importado em [alerts.py:46](../assets/plugin_examples/gowa/alerts.py#L46); `_CFG` em [:65](../assets/plugin_examples/gowa/alerts.py#L65));
   - `logger.info("gowa alert: grupo virou supergrupo — chat_id %s → %s (auto-atualizado)", payload["chat_id"], new_id)`;
   - montar `retry_payload = {**payload, "chat_id": new_id}` e **reexecutar uma única vez** o mesmo `method` (chamada recursiva controlada por um flag `_migrated=True`/param default para não recorrer em loop se o novo id também falhar).
   - devolver o resultado do retry.
3. `[sequencial]` Guarda anti-loop: reexecutar **no máximo uma vez** por chamada (um supergrupo não migra de novo em cadeia). Se o retry também trouxer migração, apenas logar e devolver o erro (não recorrer).

**Pronto quando:**
- Simulando (mock httpx ou stub de `_tg_call`) uma 1ª resposta `{"ok":false,"parameters":{"migrate_to_chat_id":-100999}}` seguida de `{"ok":true,"result":{"message_id":42}}`: `_tg_send` retorna `42`, e `config_repo.get("plugin.gowa.disconnect_alert_chat_id")` passa a valer `"-100999"`.
- Log INFO com o de→para aparece uma vez.

#### Status de execução — Fase F1
**Estado:** ✅ Concluída
- **O que foi feito:** `_tg_call` em [assets/plugin_examples/gowa/alerts.py](../assets/plugin_examples/gowa/alerts.py#L215) ganhou o parâmetro `_migrated=False` e o bloco de interceptação: ao ver `data.parameters.migrate_to_chat_id` com `payload["chat_id"]` presente e `not _migrated`, persiste o novo id (`asyncio.to_thread(config_repo.set, ...)`), loga INFO com o de→para e reexecuta `_tg_call` uma única vez com `_migrated=True`. Espelhado em `storages/plugins/gowa/alerts.py`.
- **Como foi feito / decisões:** guarda anti-loop via flag default `_migrated` (recursão única). Cobre `sendMessage`/`editMessageText`/`deleteMessage` de graça (todos passam por `_tg_call`). `config_repo` e `_CFG` já estavam no módulo — sem import novo.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `tests/test_gowa_alert_migration.py` (novo) — 11/11 verde: retorno de sucesso (mid=42), retry usa o novo id, config persistida uma vez, anti-loop no 2º erro, erro comum não migra, payload sem `chat_id` não migra. `py_compile` + import limpo do módulo.

---

### Fase F2 — Higiene do estado agregado na troca de chat (C2) 🔴

**Objetivo:** ao mudar o `chat_id` (por migração ou manualmente), não tentar apagar/editar a mensagem antiga no chat defunto — postar uma nova no destino atual.

**Itens:**
1. `[sequencial]` Após carregar `agg` em `_tick` ([alerts.py:381](../assets/plugin_examples/gowa/alerts.py#L381)), computar `chat_changed = bool(agg.get("telegram_chat_id")) and agg.get("telegram_chat_id") != cfg["chat_id"]`.
2. `[sequencial]` No ramo "há caixas fora do ar" ([alerts.py:405‑417](../assets/plugin_examples/gowa/alerts.py#L405)): quando `chat_changed`, tratar como **sem mensagem existente** — não usar `old_mid`/`old_chat` antigos para `_tg_delete`; só enviar a nova (`has_msg` efetivo = False). Isso força o reenvio (o `if has_msg and not changed and not due` em [:409](../assets/plugin_examples/gowa/alerts.py#L409) não deve barrar quando o chat mudou).
3. `[sequencial]` No ramo "tudo reconectou" ([alerts.py:388‑399](../assets/plugin_examples/gowa/alerts.py#L388)): quando `chat_changed`, **pular** o `_tg_delete` da mensagem antiga (ela vive num chat que não existe mais) e apenas limpar o estado (`telegram_message_id=None`, etc.). Enviar "reconectado" no `cfg["chat_id"]` atual se a condição de recuperação valer.

**Pronto quando:**
- Após uma migração detectada pela F1, o próximo `_tick` com caixa ainda caída **não** emite `deleteMessage` no chat antigo e posta uma mensagem nova no chat novo (verificável por log/`down_signature` + `telegram_chat_id` salvo = novo id).
- Trocar o `chat_id` manualmente na tela do plugin com um alerta ativo também posta no destino novo sem erro no log.

#### Status de execução — Fase F2
**Estado:** ✅ Concluída
- **O que foi feito:** em `_tick` ([alerts.py](../assets/plugin_examples/gowa/alerts.py#L381)), após carregar `agg`, computa `chat_changed = bool(agg.telegram_chat_id) and agg.telegram_chat_id != cfg["chat_id"]`. No ramo "tudo reconectou": o `_tg_delete` da msg antiga é pulado quando `chat_changed` (o resto — envio de "reconectado" no chat atual + limpeza do estado — segue). No ramo "há caixas fora do ar": `has_msg` vira `False` quando `chat_changed` (força reenvio, não deixa o guarda `has_msg and not changed and not due` barrar), e o `_tg_delete` da msg antiga só roda `if old_mid and not chat_changed`. Espelhado em `storages`.
- **Como foi feito / decisões:** um único `chat_changed` cobre migração (F1) **e** troca manual do `chat_id` na tela. Não deleta/edita mensagem num chat defunto — posta nova no destino corrente.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `py_compile` + import limpo. Comportamento coberto logicamente pela F1 (a migração reescreve `cfg["chat_id"]`, e o próximo `_tick` relê `cfg` → `chat_changed=True`).

---

### Fase F3 — Paridade no `/alert-test` (C3, opcional) 🟢

**Objetivo:** o botão "testar" também auto-migra quando o usuário testa num id antigo.

**Itens:**
1. `[paralelo]` Em `alert_test` ([routes.py:140‑165](../assets/plugin_examples/gowa/routes.py#L140)), após parsear `data`, checar `data["parameters"]["migrate_to_chat_id"]`; se presente: `config_repo.set(_CFG + "disconnect_alert_chat_id", novo)` e reexecutar o `POST` uma vez com o novo id; devolver `ok` do retry. `_CFG` já definido em [routes.py:25](../assets/plugin_examples/gowa/routes.py#L25).
2. `[paralelo]` Alternativa (preferível se F1 já existir): extrair a lógica de C1 para um helper compartilhável e reusar aqui — evita duplicar o contrato do Telegram. Avaliar durante a execução (ver P1).

**Pronto quando:** testar com um `chat_id` que migrou responde `{ok:true}` e o campo na tela passa a mostrar o novo id ao recarregar.

#### Status de execução — Fase F3
**Estado:** ✅ Concluída
- **O que foi feito:** `alert_test` em [routes.py](../assets/plugin_examples/gowa/routes.py#L140): após parsear `data`, se `data.parameters.migrate_to_chat_id` presente (só quando `not ok`), persiste o novo id (`asyncio.to_thread(config_repo.set, _CFG + "disconnect_alert_chat_id", ...)`) e reenvia o `POST` uma vez com o novo `chat_id` dentro do mesmo `AsyncClient`. Espelhado em `storages`.
- **Como foi feito / decisões:** **P1 resolvida = (b) duplicar a checagem de 3 linhas.** `routes.py` usa `httpx` cru (não `_tg_call`) e o payload do teste difere do loop; importar um helper de `alerts.py` acoplaria os dois módulos por 3 linhas. A checagem do campo `migrate_to_chat_id` é o contrato mínimo e fica self-contained.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `py_compile` + import limpo de `routes.py`. Retry único (não recorre) por construção — só um reenvio no `if new_id`.

---

### Fase F4 — Testes + verificação 🟢

**Objetivo:** provar o comportamento sem depender de um supergrupo real.

**Itens:**
1. `[paralelo]` **Teste focado do helper** (novo, pequeno): mockar `httpx.AsyncClient.post` para devolver a resposta de migração na 1ª chamada e sucesso na 2ª; asserir (a) retorno de sucesso, (b) `config_repo.get(...)` atualizado, (c) uma só recursão. Pode viver num teste standalone do plugin ou em `tests/` se houver ponto de entrada (ver P2).
2. `[paralelo]` **Suíte de endpoints:** rodar `venv/bin/python -m pytest tests/ -q` (Postgres de teste via `WHATSBOT_TEST_DB_URL`) — garantir que nada regrediu; o plugin `gowa` não é exercitado pela suíte core, então o alvo aqui é **não quebrar** o boot/carregamento.
3. `[paralelo]` **Validação manual (dev):** com o plugin ligado, forçar o cenário — apontar o `chat_id` para um grupo, promovê-lo a supergrupo (ou simular retornando `migrate_to_chat_id` via um stub temporário) e confirmar no `logs/` a linha `chat_id … → … (auto-atualizado)` e a chegada da mensagem no novo chat.

**Pronto quando:** teste do helper verde, suíte verde no Postgres, e o log de auto-migração aparece no cenário manual.

#### Status de execução — Fase F4
**Estado:** ✅ Concluída (com ressalva de ambiente)
- **O que foi feito:** teste focado `tests/test_gowa_alert_migration.py` (**P2 resolvida = (a)**) mockando `httpx.AsyncClient` (fila de respostas compartilhada entre clients recursivos) e `config_repo.set` (sem DB). 11 checks: migração→retry com sucesso, retry usa o novo id, config persistida 1×, anti-loop, erro comum não migra, payload sem `chat_id` não migra.
- **Como foi feito / decisões:** teste standalone (o plugin `gowa` não é exercitado pela suíte core); segue o formato `check(...)` de `tests/test_gowa_plugin.py`. Módulo carregado via `importlib` da cópia bundled.
- **Problemas / pendências:** **a suíte de endpoints (`pytest tests/`) NÃO pôde rodar neste ambiente** — `pytest` não está instalado no `venv` e não há `WHATSBOT_TEST_DB_URL` (Postgres de teste) configurado no `.env`. Mitigação: a mudança é isolada ao plugin `gowa`, que a suíte core não exercita; validado que ambos os módulos importam limpo (safety de boot/carregamento) e `py_compile` passa. Rodar `venv/bin/python -m pytest tests/ -q` num ambiente com o Postgres de teste antes do release.
- **Verificação:** teste do helper 11/11 verde; `py_compile` verde em `alerts.py`+`routes.py`; import limpo de ambos. Validação manual em dev pendente (requer supergrupo real ou stub — coberta pelo teste automatizado).

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Recursão em `_tg_call` | Novo id também inválido → loop de retry | Reexecutar **no máximo uma vez** (flag `_migrated`); no 2º erro, logar e devolver (F1, item 3). |
| Escrita em `config` dentro de helper async | `config_repo.set` é síncrono/bloqueante | Envolver em `asyncio.to_thread` (padrão do arquivo, cf. [alerts.py:338,364](../assets/plugin_examples/gowa/alerts.py#L338)). |
| Estado `__all__` apontando chat velho | Tentar `deleteMessage` num chat defunto gasta rede/loga warning | Guarda `chat_changed` na F2 pula o delete e reposta limpo. |
| Migração só em um dos métodos | Se só o `deleteMessage` migrar mas o `sendMessage` seguinte usar `cfg["chat_id"]` já atualizado | `cfg` é relido a cada `_tick`; dentro do mesmo tick o retry da F1 já entrega no novo id e a `config` já foi persistida → próximo tick coerente. |
| Duas caixas GOWA, um só destino | O `chat_id` é global → migração afeta o alerta agregado inteiro | Correto por design (D4): uma reescrita conserta todas as caixas. |
| Segredo na URL | Token do bot fica na URL da Bot API (`/bot{token}/...`) | Comportamento **pré-existente** ([alerts.py:216](../assets/plugin_examples/gowa/alerts.py#L216)); não introduzimos novo vazamento; não logar a URL crua. |
| Restart de plugin | Mudança em `alerts.py` exige recarregar o worker | Em dev o uvicorn `--reload` recarrega ao salvar `.py`; a task supervisionada `disconnect_alert_loop` reinicia com o worker. |
| Divergência bundled ↔ storages | Patch só em `assets/` não afeta a cópia viva de dev | Aplicar em **ambas** durante o desenvolvimento (D6/§7). |

---

## 6. Perguntas em aberto

- **P1 — Extrair um helper compartilhado para F1+F3, ou duplicar a checagem?**
  ⏸️ ADIADO (decidir na execução). Contexto: F3 é opcional; se entrar, reusar a lógica de C1 evita duplicar o contrato do Telegram.
  (a) helper `_maybe_migrate(data, payload) -> new_id|None` em alerts.py, importado por routes.py; (b) duplicar a checagem de 3 linhas em `alert_test`.
  **Recomendação:** (a) se F3 for feita; senão manter tudo em `_tg_call` e não tocar routes.py.

- **P2 — Onde mora o teste do helper?**
  ⏸️ ADIADO. Contexto: `tests/test_endpoints.py` cobre o core, não o plugin `gowa`.
  (a) teste standalone `tests/test_gowa_alert_migration.py` mockando httpx; (b) sem teste automatizado, só validação manual (F4 item 3).
  **Recomendação:** (a) — o helper é puro o suficiente para um teste isolado rápido e barato; alto valor por ser o coração da correção.

- **P3 — Bump de versão do plugin `gowa` (1.0.0 → 1.0.1)?**
  ✅ DECIDIDO (2026-07-06): **opcional**, sem impacto funcional (não há migração nova nem mudança de contrato). Fazer só se o fluxo de release exigir versionar o `.zip` do plugin. Não bloqueia o plano.

---

## 7. Apêndice — arquivos-chave

**Plugin `gowa` (editar a cópia BUNDLED versionada; espelhar na viva de dev):**
- [assets/plugin_examples/gowa/alerts.py](../assets/plugin_examples/gowa/alerts.py) — F1 (`_tg_call` :215‑266) + F2 (`_tick` :337‑424). **Núcleo da correção.**
- [assets/plugin_examples/gowa/routes.py](../assets/plugin_examples/gowa/routes.py) — F3 opcional (`alert_test` :140‑165).
- `storages/plugins/gowa/alerts.py` (+ `routes.py`) — cópia viva de dev (gitignored): aplicar o mesmo patch para testar sem re-bootstrap.

**Testes:**
- `tests/test_gowa_alert_migration.py` (novo, se P2=a) — mock de httpx para o cenário de migração.
- [tests/test_endpoints.py](../tests/test_endpoints.py) — suíte de regressão (rodar, não editar).

**Sem toque:** `db/` (nenhuma migration), `channels.py`, `lifecycle.py`, `static/gowa.js` (o campo do `chat_id` já reflete o valor de `config` no próximo GET — a auto-atualização aparece sozinha).

---

## 8. Checklist de verificação

- [x] F1: envio no `chat_id` antigo detecta `migrate_to_chat_id`, persiste o novo em `config` e reexecuta com sucesso (uma única recursão).
- [x] F1: `config_repo.get("plugin.gowa.disconnect_alert_chat_id")` reflete o novo id após a migração. _(coberto no teste: config persistida com o novo id)_
- [x] F2: com alerta ativo, a troca de `chat_id` (migração **ou** manual) posta mensagem nova no destino atual e **não** tenta `deleteMessage` no chat antigo.
- [x] F3: `POST /alert-test` num id migrado persiste o novo id e reenvia → responde `{ok:true}`.
- [x] F4: teste do helper verde (mock httpx: migração → sucesso) — 11/11.
- [ ] `venv/bin/python -m pytest tests/ -q` **verde no Postgres** (`WHATSBOT_TEST_DB_URL`) — ⚠️ NÃO rodou (sem pytest/Postgres de teste neste ambiente); rodar antes do release.
- [x] Log INFO de auto-migração (`chat_id … → …`) aparece uma vez _(emitido em `_tg_call`; verificar no cenário manual/logs em dev)_.
- [x] Sem segredo novo em log/URL (token continua só na URL da Bot API, como antes).
- [x] Patch aplicado em **ambas** as cópias do plugin (`assets/…` versionada + `storages/…` viva).
- [ ] Restart do worker recarrega a task `disconnect_alert_loop` sem erro no boot — validar em dev (import limpo já confirmado).
