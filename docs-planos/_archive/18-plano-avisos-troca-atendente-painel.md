# Plano de Implementação — 18: Avisos de sistema ao trocar atendente pelo painel "?"

> Quando o operador troca o atendente pelo **botão direito** na lista de conversas, aparece o card no fio
> ("Admin atribuiu a conversa para Thiago" / "Admin assumiu a conversa" / "Admin removeu a atribuição").
> Mas quando troca pelo **painel "?"** (info da conversa → `AssigneePicker`), a atribuição muda e **nenhum
> card aparece**. Causa: a rota usada pelo painel (`assign-agent`) **nunca chama `_emit_notice`**.
>
> **Escopo:** adicionar `_emit_notice` nos 3 ramos de `assign-agent` (`user`/`none`/`ai`), com **paridade**
> exata com o botão direito (mesmos `event_type`/gates). **Sem** schema novo, **sem** RBAC novo.

---

## 0. Estado atual VERIFICADO (2026-06-22, branch `developer`)

- **Painel "?"** = [`ConversationInfoPanel.js`](../web/static/js/components/contacts/ConversationInfoPanel.js)
  renderiza [`AssigneePicker.js`](../web/static/js/components/contacts/AssigneePicker.js). `assign(payload)`
  (`:52-63`) → `assignAgent(conv.id, payload)` (`api.js:462`) → `POST /api/conversations/{id}/assign-agent`.
  Payload: `{kind: 'user'|'ai'|'none', userId?, agentKey?}`. "Atribuir a mim" usa `{kind:'user', userId: me.id}`.
- **Rota `assign_agent`** ([`conversations.py:322-379`](../server/routes/conversations.py#L322)): trata
  `kind=user|ai|none` via `conversation_repo.assign_agent`, sincroniza `contacts.ai_enabled` + WS
  `contact_ai_toggled`, e termina **só** com `await _broadcast(deps, "conversation_assigned", "conversation.assigned", conv)`.
  **Não há `_emit_notice` em nenhum dos 3 ramos** (verificado linha a linha).
- **Botão direito** (referência de paridade): `POST /assign` (`conversations.py:270-286`) →
  `conversation_repo.set_assignee` → `_emit_notice(request, conv, "assigned", target=<nome>)` quando há
  assignee, senão `_emit_notice(request, conv, "unassigned")`. E `POST /assign-me` (`:288-302`) →
  `_emit_notice(request, conv, "assigned_me")`.
- **Formatters já existem** em [`system_notices.py`](../server/system_notices.py):
  - `assigned` → `_f_assigned(actor, target)` (`:103`): "🧑‍💼 {actor} atribuiu a conversa para {target}."
  - `assigned_me` → `_f_assigned_me(actor)` (`:110`): "🧑‍💼 {actor} assumiu a conversa."
  - `unassigned` → `_f_unassigned(actor)` (`:116`): "🧑‍💼 {actor} removeu a atribuição da conversa."
  - `agent_changed` (grupo `ai`) → "mudou o agente ativo para {agent}" (usado por `POST /agent`, `:319`).
  - Grupos/gates: `assigned`/`assigned_me`/`unassigned` ∈ grupo **`assignment`** (gate `system_notice_assignment`);
    `agent_changed` ∈ grupo **`ai`** (gate `system_notice_ai`).
- `_emit_notice(request, conv, event_type, **ctx)` (`conversations.py:60-82`) resolve o autor do `current_user`
  e é **defensivo** (nunca levanta).

---

## 1. Decisões de design (travadas)

1. **Paridade com o botão direito** (escolha do usuário): para `kind=user` mostrar "atribuiu a conversa para
   {nome}" (ou "assumiu a conversa" quando for para si mesmo); para `kind=none` mostrar "removeu a atribuição".
2. **`kind=ai`** (uma IA assume pelo painel): usar o `agent_changed` existente ("mudou o agente ativo para X").
   É o formatter semântico mais próximo e já existe; cai no gate `system_notice_ai`.
3. **Não** emitir card extra de `ai_on`/`ai_off` no flip implícito de IA do `assign-agent` (evita 2 cards por
   ação). O flip de `contacts.ai_enabled` continua só com o broadcast `contact_ai_toggled` (silencioso no fio).
4. **`assigned_me` vs `assigned`:** quando `kind=user` e `user_id == current_user.id`, emitir `assigned_me`
   ("assumiu a conversa"); senão `assigned` com `target=<nome do usuário>`. (O `AssigneePicker` manda
   `kind:user` tanto no "Atribuir a mim" quanto na lista — a distinção é por `user_id`.)

---

## 2. Backend — `assign_agent` (`conversations.py:322-379`)

Logo após `if not conv: return _err(...)` e antes/depois do `_broadcast` final, adicionar por ramo:

```python
if kind == "user":
    me = current_user(request)
    if me and uid == me.get("id"):
        await _emit_notice(request, conv, "assigned_me")
    else:
        target = await asyncio.to_thread(user_repo.get, uid)
        await _emit_notice(request, conv, "assigned",
                           target=(target or {}).get("name") or f"usuário #{uid}")
elif kind == "none":
    await _emit_notice(request, conv, "unassigned")
elif kind == "ai":
    ag = await asyncio.to_thread(agent_repo.get, agent_key)
    name = (ag or {}).get("display_name") or agent_key
    await _emit_notice(request, conv, "agent_changed", agent=name)
```

Notas:
- `user_repo` e `agent_repo` já são importados/usados nas rotas vizinhas (`assign` `:281`, `set_agent` `:317`)
  — **sem novos imports**.
- `_emit_notice` é defensivo → nenhum risco de quebrar a atribuição se a formatação falhar.
- Reaproveitar `uid`/`agent_key` já resolvidos no topo do handler (não reparsear o body).

---

## 3. Frontend
**Nada obrigatório.** O `AssigneePicker` já recebe a `conversation` atualizada e o card chega via WS
`new_message` (emitido por `emit_conversation_notice`). Conferir que `ConversationInfoPanel`/`Contacts`
escutam `new_message` para a conversa aberta (já escutam).

---

## 4. Testes (`tests/test_endpoints.py`)
- `POST /assign-agent {kind:'user', user_id:X}` (X ≠ caller) → `select messages WHERE role='conversation_event'`
  contém 1 linha "atribuiu a conversa para …".
- `kind:'user', user_id == caller` → card "assumiu a conversa" (`assigned_me`).
- `kind:'none'` → card "removeu a atribuição".
- `kind:'ai', agent_key` → card "mudou o agente ativo para …" (grupo `ai`).
- Gate desligado (`system_notice_assignment=false`) → **nenhum** card para `user`/`none`.

---

## 5. Checklist
- [ ] `_emit_notice` nos 3 ramos de `assign_agent` (`user`→`assigned`/`assigned_me`, `none`→`unassigned`,
      `ai`→`agent_changed`).
- [ ] Reusar `current_user`, `user_repo.get`, `agent_repo.get` (sem imports novos).
- [ ] Testes 4.x; rodar `python tests/test_endpoints.py`.

---

## 6. Riscos
- **Duplicação de card** se o `assign-agent` também passasse a emitir `ai_on/ai_off` — evitado por decisão (3).
- **Gate por grupo:** para `kind=ai` o card cai no gate `system_notice_ai` (não `assignment`); é o
  comportamento desejado (é uma troca de agente de IA), mas vale documentar para o operador não estranhar.
