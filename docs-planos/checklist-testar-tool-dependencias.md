# Checklist — Task "Testar tool da IA com dependencias" (WHATSBOT · TECHIFY)

Roteiro de teste da task do projeto (5 itens). Os resultados e correções vão para
[QA-motor-agentes.md](QA-motor-agentes.md) no formato **testado → problema → correção**.

Quatro dos cinco itens da task já foram testados no motor de agentes — este arquivo
**mapeia a task ao que já existe** (com status), aponta os **gaps ainda abertos** e
adiciona o roteiro **novo** do item 4 (Gerar melhoria), que ainda não tinha cobertura.

## Itens da task (do print)
1. Testar se um agente passa para outro → **Teste A** (= Teste 1 do motor)
2. Testar com dependências (`requires_prior_call`) → **Teste B** (= Teste 2 do motor)
3. Testar trava de looping / limite de chamada de tool ou agente → **Teste C** (= Teste 3 do motor)
4. Testar sugestão de melhoria (Gerar melhoria) → **Teste D** (**NOVO** — sem cobertura ainda)
5. Sempre retornar para o agente padrão de triagem → **Teste E** (= Teste 4 do motor)

## Estado / setup (herdado do checklist do motor de agentes)
- `auto_reply` global = **ON** (1º gate — sem ele nada responde).
- Backend Postgres; canal GOWA (WhatsApp real). Servidor em `http://127.0.0.1:8090/`.
- Topologia hub-and-spoke: só **Jarvis (`default`)** roteia livre e escala p/ humano;
  `maestro`/`zaad` são folhas (`routing_targets=["default"]`, sem `transfer_to_human`).
- **Pendência recorrente:** o worker precisa ser reiniciado para pegar mudanças em
  `ai_engine/*` (fora do `--reload-dir` do uvicorn).
- Observabilidade: assinatura fixa no prompt de cada agente ("AQUI É VENDAS") para
  ver a olho quem respondeu; conferir no DB `executions.routing_steps` + `execution_steps`.

---

## Teste A — Um agente passa para outro (handoff) ✅ já validado
Cobertura: **Teste 1** do [QA-motor-agentes.md](QA-motor-agentes.md) (revalidado 2026-07-02).
- [x] "Quero comprar o plano premium" → Jarvis `transferir_agente` e o destino responde
      no **MESMO turno** (multi-hop). DB: `active_agent_key` gravado, card `agent_changed`.
- [x] Sem vazar `["` cru nem `[]` (bugs 1.1/1.3 corrigidos em [server/helpers.py](../server/helpers.py)).
- [x] Sem ping-pong (para por `MAX_ROUTING_DEPTH`/ciclo).
- [ ] **Gap aberto — allowlist:** instruir a triagem a transferir p/ um agente **fora**
      de `routing_targets` (ex.: `financeiro`) → a tool deve **recusar** ("não está entre
      os destinos permitidos"). Ainda não testado.

## Teste B — Dependências (`requires_prior_call`) ⚠️ parcial
Cobertura: **Teste 2** do [QA-motor-agentes.md](QA-motor-agentes.md). Feature em
[ai_engine/hooks.py](../ai_engine/hooks.py) (`check_hooks`).
- [x] `requires_prior_call` feliz: `set_custom_attribute` exige `save_contact_info` →
      bloqueado pré-dispatch até o pré-requisito rodar (exec 361/364). Bloqueio gera card
      de tool_call **sem** `tool_executed` e devolve string ao LLM, que se recupera sozinho.
- [x] Reset por-mensagem confirmado (o pré-requisito da msg anterior **não** conta).
- [ ] **Gap — cadeia A→B→C:** tool C exige B, B exige A; pedir C direto → deve exigir B,
      depois A, na ordem. Validar a mensagem de bloqueio de cada nível.
- [ ] **Gap — pré-requisito inexistente:** `requires_prior_call: "tool_que_nao_existe"` →
      confirmar que a tool **nunca** libera (e não quebra o turno).
- [ ] **Gap — `hooks_config` malformado** (não-dict, valores errados): `check_hooks` é
      defensivo (não levanta, não bloqueia) — cobrir por `tests/test_hooks.py` já existe;
      confirmar em runtime que um config quebrado não derruba o dispatch.
- [ ] **Limitação documentada (não é bug):** escopo é **por-mensagem**, não por-conversa
      → fluxos de "autenticar uma vez só" re-exigem o pré-req a cada mensagem. Ver
      "Sugestões de melhoria" (escopo por CONVERSA).

## Teste C — Trava de loop / limite de tool ou agente ⚠️ parcial
Cobertura: **Teste 3** do [QA-motor-agentes.md](QA-motor-agentes.md).
- [x] `call_limit` por tool: `{"reminder_create": {"call_limit": 2}}`, pedir 4 → executa
      **exatamente 2×**, 3ª/4ª bloqueadas (exec 365). Texto do bloqueio corrigido para
      "nesta mensagem…" ([ai_engine/hooks.py](../ai_engine/hooks.py)) — **re-testar após restart**.
- [x] Trava de hops de agente: `MAX_ROUTING_DEPTH=5` + ciclo (`if nxt in seen: break`) +
      no-op (`nxt == current`) em [ai_engine/routing.py](../ai_engine/routing.py). Ping-pong
      A→B→A para sozinho.
- [ ] **Gap — loop real de tool SEM `call_limit`:** criar uma tool de plugin que sempre
      devolva "chame de novo" e ver se o AGNO trava sozinho ou repete indefinidamente.
      Cronometrar e contar `Tool call for …` no log.
      - **Documentar:** existe trava de **hops de agente** (`MAX_ROUTING_DEPTH`) mas **não**
        há guarda global de **iterações de tool-call por agente** no código do WhatsBot —
        depende do comportamento interno do AGNO. Se o AGNO não proteger, é risco.
- [ ] **Gap — `call_limit` do default (`transferir_agente`):** confirmar que o default de
      engine `{"transferir_agente": {"call_limit": 1}}` impede 2 transferências no mesmo hop
      e que um agente que define o próprio hook **sobrepõe** o default.

## Teste D — Sugestão de melhoria (Gerar melhoria) 🆕 SEM cobertura ainda
Feature: [app/services/improvement_service.py](../app/services/improvement_service.py) →
rota `POST /api/contacts/{phone}/improve` em [server/routes/contacts.py:934](../server/routes/contacts.py#L934).
Chamada **one-shot, NÃO-agêntica** (client OpenAI sync direto, fora do motor AGNO).
Fluxo no painel: botão direito numa resposta da **IA** → "Gerar melhoria" → (opcional)
escrever o que saiu errado → resultado salvo como card `role="system"` (painel-only) e
transmitido via WS `new_message`.

**O que a análise monta** (conferir no card gerado): (1) prompt do agente **ativo**
daquele contato, (2) tools disponíveis (só as `enabled`), (3) tools **realmente usadas**
na resposta (janela `[started-5s, completed+15s]` da execução), (4) histórico recente
com a resposta marcada `⟵ RESPOSTA MARCADA COMO INCORRETA`, (5) feedback do operador.
Saída em markdown com **Diagnóstico** + **Recomendações**.

> **STATUS 2026-07-02 — TODOS os itens verificados (ver [QA-motor-agentes.md](QA-motor-agentes.md) → "Teste D").**
> D1/D5/D7/D10 validados ao vivo; D4 no input; D2/D3/D6/D8/D9/D11 por código. ✅
> 🐞 **Achado D.A (bug):** o card é salvo na conversa/canal ERRADO — `improve_message` usa
> `_get_contact(phone)` com `channel_id="default"`, então para um contato cuja conversa
> ativa não está no inbox default o card cria uma **conversa fantasma no inbox 1** (vimos
> 2 conversas do mesmo número no painel). Fix: resolver o canal pelo `conversation_id` da
> resposta marcada e salvar o card naquela conversa. **Não implementado ainda.**

- [x] **D1 — Happy path:** marcar uma resposta da IA + escrever feedback → gerar. Esperar
      card "🔧 Análise de melhoria" com seções **Diagnóstico** e **Recomendações**.
- [ ] **D2 — Painel-only:** o card **não** é enviado ao WhatsApp e **não** entra no contexto
      do LLM (`role="system"` está em `excluded` no [message_repo.py:84](../db/repositories/message_repo.py#L84)).
      Mandar a próxima mensagem do cliente e confirmar que a IA **não** "vê" a análise.
- [ ] **D3 — Feedback opcional:** gerar **sem** texto de feedback → deve funcionar usando
      "(o operador não detalhou o que saiu errado)".
- [ ] **D4 — Ferramentas usadas:** testar numa resposta que **usou** tool (deve listar
      `tool(args)`) e numa que **não usou** (deve dizer "Nenhuma ferramenta foi usada").
      Cobre `_find_tools_used_around` (best-effort; degrada p/ `[]` sem quebrar).
- [ ] **D5 — Multi-agente:** numa conversa presa num especialista (ex.: `maestro`), o
      "Prompt principal do agente" na análise deve ser o do **maestro** (via
      `agent_factory.build_for_contact`), não o do `default`.
- [ ] **D6 — Modelo:** resolução `improvement_model` (config / env `WHATSBOT_IMPROVEMENT_MODEL`)
      → senão modelo do agente → senão `DEFAULT_MODEL`. Setar `improvement_model` e conferir.
- [ ] **D7 — Usage:** confirmar linha em `usage` com `call_type="improvement"` (tokens/custo).
- [ ] **D8 — Permissão (RBAC):** usuário sem `conversation.reply` → **403**. Com RBAC
      off / single-password → passa (default-allow).
- [ ] **D9 — Fallbacks (ainda salvam card):** sem API key → card "[WhatsBot] API key não
      configurada."; erro do LLM → card "[WhatsBot] Falha ao gerar a análise de melhoria: …".
- [ ] **D10 — Validação de entrada:** `message.content` vazio → `_err("Mensagem inválida
      para análise.")` (não chama o LLM).
- [ ] **D11 — Modo escuro:** card `system` legível no tema escuro (SystemMessageCard usa `wa-*`).

## Teste E — Retorno ao agente padrão de triagem ⚠️ NÃO é automático (gap central da task)
Cobertura: **Teste 4** do [QA-motor-agentes.md](QA-motor-agentes.md).
- [x] Via **fechar** a conversa: `set_status("closed")` zera `active_agent_key`
      ([conversation_repo.py](../db/repositories/conversation_repo.py)) → mensagem nova cai
      no Jarvis (triagem). Validado (exec 367).
- [x] Via **`transferir_agente(default)`** explícito do especialista (hub-and-spoke).
- [ ] ⚠️ **GAP vs. a task ("SEMPRE retornar para triagem"):** hoje **não existe** retorno
      automático (por inatividade / fim de assunto). A conversa **gruda** no especialista até
      alguém fechar OU ele transferir de volta. O requisito da task **não é atendido** pelo
      comportamento atual — decidir com o time se vira feature (ver "Sugestões de melhoria").

---

## Gaps / features candidatas (saída desta rodada)
- **Retorno automático ao triagem** (item 5 da task): por config do agente ("após N min
  ocioso" ou "ao resolver o assunto"). **Não existe hoje** — maior lacuna da task.
- **Guarda global de iterações de tool-call por agente** (máx. N por execução) — hoje só
  há `MAX_ROUTING_DEPTH` (hops de agente) e `call_limit` (por tool/mensagem).
- **Escopo de `requires_prior_call`/`call_limit` por CONVERSA**, não por mensagem — para
  fluxos multi-turno (autenticar uma vez só).
- **Allowlist de destino** (`routing_targets`) — validar a recusa explícita (Teste A).

## Como rodar
Preciso do WhatsApp conectado no GOWA + número de teste. O usuário dispara os cenários no
painel; eu verifico `messages` / `executions` / `execution_steps` / `usage` no DB e monto
o relatório (testado → problema → correção) no [QA-motor-agentes.md](QA-motor-agentes.md).
