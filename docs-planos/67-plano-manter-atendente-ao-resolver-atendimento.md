# Plano 67 — Resolver atendimento sem desatribuir o atendente (costura genérica no core + opt-in do plugin protocolos)

> **Status:** IMPLEMENTADO (aguardando deploy core + publicação do .zip) · **Data:** 2026-07-21 · **Escopo:** pequeno/médio
> **Origem:** pedido do usuário (Empresa Exemplo prod) — ao clicar "Resolver atendimento" no painel, o core desatribui o atendente da conversa; ele quer uma opção (espelhando a que já existe para *finalizar protocolo*) para **manter** o atendente atribuído ao resolver. Relacionado ao plano 54 (arquivar/atribuir por conversa) e à investigação da sessão. **Método:** leitura do core (`conversation_service`/`conversation_repo`) + do plugin protocolos (`filters.py`/`logic.py`/`config.js`) + grep de callers e do `apply_filter`, tudo com `arquivo:linha` verificado.
> Adiciona um **seam genérico** no core (`filter.conversation.clear_assignee_on_close`, default = limpar) que qualquer plugin pode hookar para preservar o assignee humano ao fechar a conversa. O plugin protocolos ganha um setting opt-in (`resolve_keep_assignee`, default OFF) + toggle na UI. Sem `if plugin ==` no core; comportamento byte-idêntico quando ninguém hooka.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| D1 ✅ (2026-07-21) | Abordagem = **Opção B** (costura genérica no core + opt-in do plugin), não Opção A (reatribuir pós-fechar no plugin) | Discutido na sessão. A recomendada por não "brigar com o core" (sem double-write, sem flicker no WS, sem restore) |
| D2 ✅ (2026-07-21) | O seam controla **apenas `assignee_user_id`**. `active_agent_key` continua **SEMPRE limpo** no close (inalterado) | Preserva a cascata de fallback de agente do core (CLAUDE.md: reabrir sem `active_agent_key` cai no agente MARCADO). Não arriscar essa lógica |
| D3 ✅ (2026-07-21) | Nome do filtro: `filter.conversation.clear_assignee_on_close` · valor `bool`, default `True` (limpar). Param do repo: `set_status(conv_id, status, *, clear_assignee=True)` | Nomes precisos ("assignee" = humano, não "assignment"); default = comportamento atual |
| D4 ✅ (2026-07-21) | Setting do plugin: `plugin.protocolos.resolve_keep_assignee`, default **`False`** (mantém comportamento hoje = desatribui). ON = mantém atendente | Opt-in explícito; instalações existentes não mudam sozinhas |
| D5 ✅ (2026-07-21) | Desatribuir ao **FECHAR PROTOCOLO** fica **fora de escopo** (follow-up) | Este plano resolve só a dor imediata (resolver atendimento). Ver §10 P3 |
| D6 ✅ (2026-07-21) | Instância-alvo roda a branch `developer` (Coolify) → o core é deployável. Core via git; plugin via `.zip` | Dois artefatos coordenados (ver §8 Fase 4) |

---

## 1. Resumo executivo

Ao **Resolver atendimento** no painel, o core sempre zera `assignee_user_id` da conversa (linha hardcoded no repo). O plugin protocolos não tem hoje como impedir isso — seu único hook nesse ponto (`filter.conversation.before_status`) só **aborta** o fechamento, não modifica o assignee.

A solução adiciona um **seam genérico** no core: antes de limpar o assignee no fechamento, o serviço consulta um novo filtro `filter.conversation.clear_assignee_on_close` (default `True` = limpar). O repo `set_status` ganha o kwarg `clear_assignee` para **não** zerar quando o filtro devolver `False`. O plugin protocolos registra esse filtro, lendo um novo setting `resolve_keep_assignee` (default OFF), e adiciona o toggle na tela de configuração. Efeito colateral bom: como o assignee nunca é apagado, ao **reabrir** a conversa a pessoa volta atribuída ("Ana volta atribuída") sem código extra.

---

## 2. Como funciona hoje (mapa)

Fluxo do botão "Resolver atendimento" (já mapeado na investigação da sessão):

| Passo | Onde | O que faz |
|---|---|---|
| 1. Popup de resolução | [protocolos/static/extends.js:81-141](../storages/plugins/protocolos/static/extends.js#L81-L141) | Intercepta `filter.conversation.beforeResolve`, abre `ResolveForm`, faz `POST /atendimentos/{id}/resolve` (fecha o **ciclo**, grava campos). **Não desatribui.** Retorna `atend` → o core segue |
| 2. Core fecha a conversa | [server/routes/conversations.py:410](../server/routes/conversations.py#L410) | `conv_svc.set_status(deps, _conv, "closed", ...)` |
| 3. Gate `before_status` | [conversation_service.py:187-194](../app/services/conversation_service.py#L187-L194) | Aplica `filter.conversation.before_status` (o plugin pode abortar; **não** altera assignee) |
| 4. **Write que zera** | [conversation_service.py:196](../app/services/conversation_service.py#L196) → [conversation_repo.py:750-753](../db/repositories/conversation_repo.py#L750-L753) | `set_status` grava `status=closed`, `resolved_at`, **`assignee_user_id=None`**, `active_agent_key=None` — **incondicional** |
| 5. Broadcast | [conversation_service.py:200-201](../app/services/conversation_service.py#L200-L201) | `_broadcast("conversation_status_changed", ..., updated)` — hoje sai com `assignee=None` |

⚠️ **Gotchas confirmados:**
- `conversation_repo.set_status` tem **um único caller** de conversas (`conversation_service.set_status`); os outros `set_status` do grep são de *outros* repos (`tool_repo`, `channel_repo`) — verificado. Logo, adicionar um kwarg com default é seguro e byte-idêntico. [conversation_repo.py:734](../db/repositories/conversation_repo.py#L734)
- `_update` devolve a **linha completa** relida do banco ([conversation_repo.py:727](../db/repositories/conversation_repo.py#L727)); quando **não** zerarmos o assignee, o `updated` do broadcast já carrega o assignee preservado — **o WS não precisa de mudança** (o problema do flicker da Opção A não existe aqui).
- `apply_filter` trata **`None` como ABORTAR** a cadeia e devolve `None` ao caller ([plugins/events.py:553-558](../plugins/events.py#L553-L558)). Um filtro booleano devolvendo `False` é um valor **não-`None`** → passa normalmente. Mas o core precisa distinguir `None` (abort/ausência) de `False`: **`clear = True if result is None else bool(result)`** (None → default seguro = limpar).
- Setting existente análogo (para **finalizar protocolo**, aba "Configurações gerais"): `auto_assign_conversation_on_close` — [logic.py:2445-2446](../storages/plugins/protocolos/logic.py#L2445-L2446), UI [config.js:376-387](../storages/plugins/protocolos/static/config.js#L376-L387). É **independente** do novo setting (um age no fechar protocolo, o outro no resolver conversa).

---

## 3. Inventário / análise

| Item | Arquivo:linha | O que falta | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|
| Param no repo | [conversation_repo.py:734-756](../db/repositories/conversation_repo.py#L734-L756) | `set_status` zera assignee incondicionalmente | Adicionar `*, clear_assignee: bool = True`; só incluir `assignee_user_id=None` no `values` quando `clear_assignee` (D2: `active_agent_key=None` continua sempre) | baixo | S |
| Seam no serviço | [conversation_service.py:187-201](../app/services/conversation_service.py#L187-L201) | Nenhum ponto de decisão sobre limpar assignee | No ramo `status=="closed"`, após `before_status`, aplicar `filter.conversation.clear_assignee_on_close` (value `True`, `ctx_extras={conversation_id, user_id}`); computar `clear = True if r is None else bool(r)`; passar `clear_assignee=clear` ao repo | baixo | S |
| Doc do filtro (CLAUDE.md) | `CLAUDE.md` (tabela "Filters disponíveis") | Filtro novo não documentado | Adicionar 1 linha na tabela de filtros | baixo | S |
| Filtro no plugin | [protocolos/filters.py:25-30](../storages/plugins/protocolos/filters.py#L25-L30) | Não registra o novo filtro | Registrar `"filter.conversation.clear_assignee_on_close": logic.clear_assignee_on_close` | baixo | S |
| Handler + setting no plugin | [logic.py:2445-2483](../storages/plugins/protocolos/logic.py#L2445-L2483) | Sem `resolve_keep_assignee` nem handler | `resolve_keep_assignee_enabled()` (default False); handler `clear_assignee_on_close(ctx, value)` → devolve `False` se enabled, senão `value`; incluir a chave em `get/set_general_config` | baixo | S |
| UI toggle | [config.js:79,372-388](../storages/plugins/static/config.js) | Sem checkbox | Novo checkbox + chave em `GENERAL_EMPTY`; ver §10 P1 (aba) | baixo | S |
| Testes core | `tests/` | Sem cobertura do seam | 2 casos: fechar sem filtro (zera) / com filtro `False` (mantém) | baixo | S |
| Testes plugin | `tests/` (protocolos) | Sem cobertura do toggle | setting ON → filtro devolve False; OFF → devolve value | baixo | S |

### Falsos positivos descartados

| Suspeita | Por que NÃO é o ponto |
|---|---|
| "O plugin desatribui no `/resolve`" | `resolve_atendimento` ([logic.py:2128-2189](../storages/plugins/protocolos/logic.py#L2128-L2189)) **nunca** faz unassign — só reatribui se o campo "atendente" mudar. A causa é o `/status` do core (passo 4) |
| "Basta o `before_status` retornar o payload para manter o assignee" | `before_status` só aborta (retorna `None`) ou libera; o write posterior zera de qualquer jeito. Não é ponto de modificação de assignee |
| "Precisa re-emitir o WS com o assignee (Opção A)" | Não. Como não zeramos, o `updated` do broadcast já carrega o assignee — sem flicker, sem segundo write |
| "O seam deveria preservar `active_agent_key` também" | D2: não. Preservar o agente ativo arriscaria a cascata de fallback do core. O seam é só do assignee humano |
| Setting `auto_assign_conversation_on_close` resolveria | Ele age no **finalizar protocolo**, não no resolver conversa — momentos distintos. São toggles independentes |

---

## 4. Contrato do seam (referência para o executor)

**Core — `conversation_service.set_status`** (só no ramo `status == "closed"`, após o `before_status`):

```python
# pseudo — nomes exatos por D3
r = await apply_filter(
    "filter.conversation.clear_assignee_on_close",
    True,
    ctx_extras={"conversation_id": conv_id, "user_id": actor_id},
)
clear_assignee = True if r is None else bool(r)   # None (abort/ausente) → default seguro
updated = await asyncio.to_thread(
    conversation_repo.set_status, conv_id, status, clear_assignee=clear_assignee)
```

**Core — `conversation_repo.set_status`** (D2 — `active_agent_key` sempre limpo):

```python
def set_status(conv_id, status, *, clear_assignee: bool = True):
    values = {"status": status}
    if status == "closed":
        values["resolved_at"] = time.time()
        values["active_agent_key"] = None          # sempre
        if clear_assignee:
            values["assignee_user_id"] = None       # condicional
    elif status == "open":
        values["resolved_at"] = None
    return _update(conv_id, values)
```

**Plugin — `logic.clear_assignee_on_close`** (assinatura de filtro `(ctx, value)`):

```python
def clear_assignee_on_close(ctx, value):
    # ON → manter atendente (não limpar) => devolve False
    # OFF → comportamento atual => devolve o value recebido (True)
    return False if resolve_keep_assignee_enabled() else value
```

Regra de default: `resolve_keep_assignee` default **`False`** → `clear_assignee_on_close` devolve `value` (True) → core limpa (idêntico a hoje).

---

## 5. Fases / Roadmap

### Diagrama de dependências

```
WAVE 0   F0 (caracterização core) 🔴        · F2 (plugin: setting+filtro+UI) 🟢
            │ (barreira: F0 antes de F1)        │ (independente — registra nome + lê setting)
WAVE 1   F1 (core: seam repo+serviço+doc) 🔴    │
            └───────────────┬──────────────────┘
WAVE 2   F3 (integração e2e + testes + zip) 🔴  [depende de F1 e F2]
```

### Tabela de fases

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | F0 | Caracterização do fechamento (core) | 🔴 [bloqueia F1] | baixo | Teste captura "close zera assignee" (verde) |
| 0 | F2 | Plugin: setting + filtro + UI | 🟢 [paralelo] | baixo | Handler devolve False/True por setting; toggle salva/carrega |
| 1 | F1 | Core: param no repo + seam no serviço + doc | 🔴 [depende de F0] | baixo | Suíte core verde; caracterização atualizada cobre `clear_assignee=False` |
| 2 | F3 | Integração e2e + testes plugin + gerar `.zip` | 🔴 [depende de F1, F2] | baixo | Resolver com toggle ON mantém atendente end-to-end |

Disciplina do repo a seguir: **verde a cada fase**; **caracterização ANTES** de mexer no fluxo crítico de fechamento (F0 antes de F1); **um refactor por commit**; nunca avançar com teste vermelho não explicado.

---

### Fase 0 — Caracterização do fechamento (core)

**Objetivo:** travar o comportamento atual antes de tocar o fluxo de close.

**Itens:**
- [sequencial] Adicionar/localizar em `tests/` um caso que: cria conversa com `assignee_user_id` setado, chama `POST /api/atendimentos/{id}/status {status:"closed"}`, e asserta `assignee_user_id is None` **e** `active_agent_key is None` após o fechamento. Referência de fluxo: [conversations.py:393-416](../server/routes/conversations.py#L393-L416).
- [sequencial] Rodar a suíte contra o Postgres de teste (`WHATSBOT_TEST_DB_URL`) e confirmar verde.

**Pronto quando:** o teste de caracterização passa e documenta o zeramento atual (será estendido em F1).

#### Status de execução — Fase 0
**Estado:** ✅ Concluída
- **O que foi feito:** a caracterização do close já existia em [tests/characterization/test_lifecycle_characterization.py](../tests/characterization/test_lifecycle_characterization.py) (`test_assign_then_close`, golden `lifecycle_assign_then_close`) — trava "close limpa assignee". Não precisou de arquivo novo.
- **Como foi feito / decisões:** reutilizada a caracterização existente em vez de duplicar. O caso `clear_assignee=False` virou teste dedicado (Fase 1).
- **Problemas / pendências:** `test_assign_then_close` **falha de forma PRÉ-EXISTENTE** (confirmado com `git stash` das minhas mudanças — falha idêntica sem elas). Causa: drift do seed `active_agent_key="default"` no passo *assign* (campo não relacionado ao plano 67; o facet de close bate byte-a-byte). Não é regressão desta entrega.
- **Verificação:** golden de close (`assignee_user_id=null`) confere; a divergência é só no seed do passo assign, pré-existente.

---

### Fase 1 — Core: seam genérico (repo + serviço + doc)

**Objetivo:** permitir fechar sem zerar o assignee, via filtro genérico, sem `if plugin ==`.

**Itens:**
- [sequencial] **Repo** [conversation_repo.py:734-756](../db/repositories/conversation_repo.py#L734-L756): adicionar `*, clear_assignee: bool = True`; mover `assignee_user_id=None` para dentro do `if clear_assignee` (D2: `active_agent_key=None` permanece sempre no close). Atualizar o docstring (linhas 735-748) para refletir que o drop do assignee agora é condicional.
- [sequencial] **Serviço** [conversation_service.py:187-201](../app/services/conversation_service.py#L187-L201): no ramo `status=="closed"`, após o `before_status`, aplicar `filter.conversation.clear_assignee_on_close` (value `True`, `ctx_extras={"conversation_id": conv_id, "user_id": actor_id}`), computar `clear = True if r is None else bool(r)` e passar `clear_assignee=clear` ao repo (§4). Atualizar o docstring do método (linhas 170-183) citando o novo filtro.
- [paralelo] **Doc** `CLAUDE.md`: adicionar 1 linha na tabela "Filters disponíveis" — `filter.conversation.clear_assignee_on_close` | `conversation_service.set_status` (no close, após `before_status`) | `bool` (default `True`) | `None`/ausente ⇒ limpa (default) | `conversation_id, user_id`.
- [sequencial] Estender a caracterização (F0): novo caso via um filtro de teste registrado que devolve `False` → assertar `assignee_user_id` **preservado** e `active_agent_key is None`.

**Pronto quando:** suíte core verde; fechar sem filtro zera (idêntico a hoje); fechar com filtro `False` preserva o assignee e ainda limpa o agente ativo.

#### Status de execução — Fase 1
**Estado:** ✅ Concluída
- **O que foi feito:**
  - [conversation_repo.py:734-761](../db/repositories/conversation_repo.py#L734-L761): `set_status(conv_id, status, *, clear_assignee=True)`; `assignee_user_id=None` agora sob `if clear_assignee`; `active_agent_key=None` sempre no close (D2). Docstring atualizado.
  - [conversation_service.py](../app/services/conversation_service.py): no ramo close, após `before_status`, aplica `filter.conversation.clear_assignee_on_close` (value `True`, `ctx_extras={conversation_id, user_id}`), `clear = True if keep is None else bool(keep)`, passa `clear_assignee=clear` ao repo. Docstrings de módulo e método atualizados.
  - [plugins/events.py](../plugins/events.py): novo nome em `KNOWN_FILTERS` (evita warning "unknown filter").
  - [CLAUDE.md](../CLAUDE.md): +1 linha na tabela "Filters disponíveis".
  - Teste novo [tests/test_plano67_keep_assignee_on_close.py](../tests/test_plano67_keep_assignee_on_close.py): 3 casos (filtro False → preserva assignee + limpa agente; default → limpa; None → default seguro limpa).
- **Como foi feito / decisões:** `None` do filtro cai no default seguro (limpar). Cliente de teste autenticado como operador admin (pós-plano-48 o route `/status` exige auth).
- **Problemas / pendências:** nenhuma. Único caller de `set_status` de conversa é o serviço → kwarg default = byte-idêntico.
- **Verificação:** `test_plano67_keep_assignee_on_close` 3/3 verde; `test_human_gate`, `test_agent_default`, `test_conversation_race` verdes (tocam assignee/agente).

---

### Fase 2 — Plugin protocolos: setting + filtro + UI (paralela a F0/F1)

**Objetivo:** opt-in do plugin ao seam, com toggle na tela de configuração.

**Itens:**
- [paralelo] **logic.py** ([2445-2483](../storages/plugins/protocolos/logic.py#L2445-L2483)):
  - `resolve_keep_assignee_enabled() -> bool` lendo `_general_key("resolve_keep_assignee")` default `False` (espelha `auto_assign_conversation_on_close_enabled`).
  - handler `clear_assignee_on_close(ctx, value)` → `return False if resolve_keep_assignee_enabled() else value` (§4).
  - incluir `"resolve_keep_assignee"` em `get_general_config` (2464) e gravar em `set_general_config` (2472) com o padrão "só grava quando presente" (como `relink_prompt_enabled`, 2477).
- [paralelo] **filters.py** ([25-30](../storages/plugins/protocolos/filters.py#L25-L30)): registrar `"filter.conversation.clear_assignee_on_close": logic.clear_assignee_on_close`. Atualizar o docstring do módulo.
- [paralelo] **config.js**: adicionar `resolve_keep_assignee: false` em `GENERAL_EMPTY` ([config.js:79](../storages/plugins/protocolos/static/config.js#L79)) e um checkbox (label "Manter atendente atribuído ao resolver atendimento" / subtítulo "Quando ativo, resolver o atendimento não remove o atendente da conversa; ele continua atribuído ao reabrir."). Local do checkbox = ver §10 P1 (recomendação: aba "Configurações gerais", junto do `auto_assign`). Usar `wa-*`/`.wa-field` (modo escuro).
- [paralelo] **plugin.yaml**: bump de versão (patch) para o upgrade version-aware do bundle não é aplicável aqui (protocolos não é bundled), mas manter o versionamento coerente com o `.zip` publicado.

**Pronto quando:** `GET/PUT /api/plugins/protocolos/general-config` inclui `resolve_keep_assignee`; o toggle carrega/salva; o handler devolve `False` quando ON e `value` quando OFF (unit).

#### Status de execução — Fase 2
**Estado:** ✅ Concluída
- **O que foi feito:**
  - [logic.py](../storages/plugins/protocolos/logic.py): `resolve_keep_assignee_enabled()` (default `False`), handler `clear_assignee_on_close(ctx, value)` (`False` se ON, senão `value`), chave em `get_general_config`/`set_general_config` (só grava quando presente).
  - [filters.py](../storages/plugins/protocolos/filters.py): registra `filter.conversation.clear_assignee_on_close` + docstring.
  - [config.js](../storages/plugins/protocolos/static/config.js): `resolve_keep_assignee: false` em `GENERAL_EMPTY` + checkbox "Manter atendente atribuído ao resolver atendimento" (aba "Configurações gerais", ao lado do `auto_assign` — P1(a)), classes `wa-*`.
  - [plugin.yaml](../storages/plugins/protocolos/plugin.yaml): versão 1.16.0 → **1.17.0**.
  - Teste novo [tests/test_plano67_protocolos_toggle.py](../tests/test_plano67_protocolos_toggle.py): OFF/default → handler devolve value; ON → devolve False; round-trip do general-config (não zera defaults).
- **Como foi feito / decisões:** P1 resolvido por (a) — aba "Configurações gerais" (a aba "Resolver atendimento" é field-builder, não comporta toggle).
- **Problemas / pendências:** nenhuma.
- **Verificação:** `test_plano67_protocolos_toggle` 3/3 verde; `node --check config.js` OK.

---

### Fase 3 — Integração e2e + testes do plugin + empacotamento

**Objetivo:** validar o caminho completo e empacotar o plugin.

**Itens:**
- [sequencial] Teste do plugin: com `resolve_keep_assignee=True`, `clear_assignee_on_close` devolve `False`; com default, devolve `value`. (Se houver rig de plugin em `tests/`, plugar ali; senão, unit direto na função.)
- [sequencial] E2e manual/integração: conversa atribuída à "Ana" → **Resolver atendimento** com toggle ON → `assignee_user_id` permanece; **reabrir** → continua atribuída à Ana. Com toggle OFF → desatribui (comportamento atual). Ver "Checklist de verificação".
- [sequencial] Gerar o `.zip` do plugin (`GET /api/plugins/protocolos/export`) e registrar no repo de plugins do Pro (`whatsbot-pro-plugins`), conforme convenção da memória [[whatsbot-pro-plugins-repo]] / [[plugin-changes-distributed-via-zip]].
- [sequencial] Deploy do core (branch `developer` → Coolify) **antes** de o toggle ter efeito em produção (o filtro do plugin é no-op enquanto o core não aplicar o seam).

**Pronto quando:** resolver com ON mantém o atendente end-to-end na instância; suíte verde; `.zip` publicado.

#### Status de execução — Fase 3
**Estado:** 🟡 Em andamento (código pronto e testado; falta deploy + publicação)
- **O que foi feito:** `.zip` do plugin gerado (`protocolos-1.17.0.zip`, exclui `__pycache__`/`.db`) no scratchpad da sessão. Testes de integração via `build_app` (Postgres de teste) verdes.
- **Como foi feito / decisões:** zip montado espelhando a lógica do endpoint de export.
- **Problemas / pendências:**
  1. **Deploy do core** (branch `developer` → Coolify) — pendente de commit/push (não commitei; aguardando o usuário).
  2. **Publicar o `.zip`** no repo `whatsbot-pro-plugins` e/ou importar em prod pela tela Plugins → reiniciar. O filtro é no-op benigno enquanto o core não tiver o seam.
  3. E2e manual em prod (resolver com toggle ON mantém atendente; reabrir mantém) — após deploy.
- **Verificação:** unit/integração local verde; e2e em prod pendente de deploy.

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| `apply_filter` trata `None` como abort | Um filtro que retorne `None` por engano viraria "não limpar" (ou pior, ambíguo) | Core faz `clear = True if r is None else bool(r)` — `None` cai no **default seguro** (limpar). Documentar |
| Kwarg novo no repo | Quebrar callers | Único caller de conversa é o serviço; default `True` = byte-idêntico. Verificado no grep (§2) |
| Preservar `active_agent_key` por engano | Quebrar a cascata de fallback de agente (reabrir cai no agente MARCADO) | D2: `active_agent_key` **sempre** limpo no close. O seam só toca `assignee_user_id` |
| Ordem de deploy (core vs plugin) | Plugin com toggle ON sem o core novo → sem efeito (silencioso) | Deploy do core primeiro (F3). O filtro é no-op benigno até o core aplicá-lo |
| Modo escuro no toggle novo | Checkbox/texto ilegível | Usar classes `wa-*` + `.wa-field`, espelhando o markup do `auto_assign` ([config.js:376-387](../storages/plugins/protocolos/static/config.js#L376-L387)) |
| Interação com auto-reopen por mensagem | Reabrir automático re-limpar assignee? | Auto-reopen usa `resolve_for_contact_ex` (outro caminho, não `set_status`); não afetado. Confirmar em F3 que reabrir manual/automático mantém o assignee preservado |
| Restart do plugin | Novo filtro só ativa após restart | Toggle do plugin/import já dispara restart; documentado no `.zip` |

---

## 7. Perguntas em aberto

**P1 — Onde fica o toggle: aba "Resolver atendimento" ou "Configurações gerais"?**
Contexto: a aba "Resolver atendimento" (`atendimento`) é hoje um **field-builder** de rótulos ([config.js:33,464](../storages/plugins/protocolos/static/config.js#L33)), sem toggles; os toggles de comportamento moram em "Configurações gerais" (`geral`), onde já está o `auto_assign_conversation_on_close`.
Opções: **(a)** "Configurações gerais", ao lado do `auto_assign` (dois toggles de assignee juntos, mesmo backend `general-config`); **(b)** injetar uma seção de toggle na aba "Resolver atendimento".
✅ **DECIDIDO (2026-07-21): (a)** — menor atrito, consistência com o setting irmão, reuso do `general-config` existente. (O pedido original citou "aba Resolver atendimento", mas essa aba não comporta toggles hoje; agrupar com o `auto_assign` é mais manutenível.) *Reavaliar com o usuário se ele preferir a aba homônima.*

**P2 — Nome visível do toggle.**
✅ **DECIDIDO:** "Manter atendente atribuído ao resolver atendimento" + subtítulo explicando o efeito no reabrir. Ajustável na revisão.

**P3 — Desatribuir ao FECHAR PROTOCOLO.**
⏸️ **ADIADO (D5):** o pedido original também menciona que a desatribuição deveria acontecer no *fechar protocolo*. Hoje `close_protocolo` ([logic.py:884-937](../storages/plugins/protocolos/logic.py#L884-L937)) **não** desatribui. Com este plano, o assignee sobrevive ao resolver; se se quiser um "limpar no finalizar protocolo", é um passo separado em `close_protocolo` (+ possível toggle). Fora de escopo aqui.

**P4 — Reabrir deve restaurar sempre?**
✅ **DECIDIDO:** de graça — como não apagamos no close, o reabrir (manual ou automático) mantém o atendente sem código extra. Confirmar em F3.

---

## 8. Apêndice — arquivos-chave

**Core (git / branch `developer`):**
- `db/repositories/conversation_repo.py` — `set_status` (+kwarg `clear_assignee`), docstring — [734-756](../db/repositories/conversation_repo.py#L734-L756)
- `app/services/conversation_service.py` — `set_status` (aplica o novo filtro), docstring — [168-217](../app/services/conversation_service.py#L168-L217)
- `CLAUDE.md` — tabela "Filters disponíveis" (+1 linha)
- `tests/` — caracterização do close + caso `clear_assignee=False`

**Plugin protocolos (`.zip` / repo whatsbot-pro-plugins):**
- `storages/plugins/protocolos/logic.py` — `resolve_keep_assignee_enabled`, `clear_assignee_on_close`, `get/set_general_config` — [2445-2483](../storages/plugins/protocolos/logic.py#L2445-L2483)
- `storages/plugins/protocolos/filters.py` — registrar o novo filtro — [25-30](../storages/plugins/protocolos/filters.py#L25-L30)
- `storages/plugins/protocolos/static/config.js` — `GENERAL_EMPTY` + checkbox — [79,372-388](../storages/plugins/protocolos/static/config.js#L79)
- `storages/plugins/protocolos/plugin.yaml` — bump de versão

---

## 9. Checklist de verificação

- [ ] `tests/` (core) verde no Postgres de teste (`WHATSBOT_TEST_DB_URL`): close sem filtro **zera** assignee; close com filtro `False` **preserva** assignee e limpa `active_agent_key`.
- [ ] Caracterização (F0) registra o comportamento atual antes de F1.
- [ ] `GET /api/plugins/protocolos/general-config` inclui `resolve_keep_assignee`; `PUT` persiste; payload antigo (sem a chave) não zera o default.
- [ ] Unit do handler do plugin: ON → `False`; OFF/default → `value`.
- [ ] E2e: resolver com ON mantém atendente; **reabrir** mantém atribuído (Ana); resolver com OFF desatribui (comportamento atual).
- [ ] WS `conversation_status_changed` no close-com-ON carrega `assignee_user_id` preservado (sem flicker "Não atribuída").
- [ ] Modo escuro legível no checkbox novo (classes `wa-*`).
- [ ] Sem `if provider ==`/`if plugin ==` no core; seam 100% genérico.
- [ ] Restart do plugin aplica o filtro; `.zip` publicado no repo de plugins.
- [ ] Deploy do core (developer/Coolify) feito **antes** de habilitar o toggle em prod.
