# Checklist — Testes do Motor de Agentes (continuar amanhã)

Roteiro dos testes que ainda faltam. Os resultados e correções vão para
[QA-motor-agentes.md](QA-motor-agentes.md) no formato **testado → problema → correção**.

> **Correção importante de análise (ler antes):** existe **roteamento multi-hop
> DENTRO do mesmo turno** ([ai_engine/routing.py](../ai_engine/routing.py)): quando um
> agente chama `transferir_agente`, o agente de destino **já responde a MESMA
> mensagem**. Há trava de loop: `MAX_ROUTING_DEPTH = 5` (máx. 5 execuções de agente
> por mensagem) + detecção de ciclo (`if nxt in seen: break`, impede A→B→A). Isso
> muda a expectativa do Teste 1 (ver abaixo).

## Estado / setup já pronto
- `auto_reply` global = **ON** (senão nada responde — é o 1º gate). ✅ conferido 2026-07-02.
- Backend Postgres; canal GOWA (WhatsApp real). Conexão via `.env` → `DATABASE_URL`.
  **Servidor roda na porta 8090** (`uvicorn server.dev` — não 8080).
- Consultar DB: `set -a; source .env; set +a` e inicializar engine com `init_engine(os.environ["DATABASE_URL"])`.
- Correções já aplicadas (Teste 1): salvamento de JSON de split truncado e `[]` vazio
  ([server/helpers.py](../server/helpers.py)); `call_limit:1` default em `transferir_agente`
  ([ai_engine/hooks.py](../ai_engine/hooks.py)).
- **Topologia dos agentes reconfigurada (2026-07-02)** — ver "Reconfiguração" no
  [QA-motor-agentes.md](QA-motor-agentes.md). Só Jarvis(`default`) roteia livre e escala p/ humano;
  `maestro`/`zaad` são folhas (`routing_targets=["default"]`, sem `transfer_to_human`). Banco limpo.
- **Pendência p/ rodar:** os 2 canais GOWA estão desconectados. Reconectar escaneando o
  QR no painel (`http://127.0.0.1:8090/` → canal `default`; QR já disponível).
- **Dica de observabilidade:** dar a cada agente uma assinatura fixa no prompt
  ("AQUI É VENDAS") para ver a olho quem respondeu.

---

## Teste 1 — Handoff entre agentes ✅ (revalidado 2026-07-02 com topologia nova — ver QA)
- [x] Enviar "quero comprar o plano premium".
  - **Esperado (corrigido):** a triagem chama `transferir_agente(vendas)` **E** o
    vendas já responde na MESMA mensagem (multi-hop). No DB: `active_agent_key='vendas'`,
    card `conversation.agent_changed`, `executions.routing_steps` registra o hop.
  - ⚠️ Ex-nota "só vale da próxima mensagem" estava **errada** — o destino responde no
    mesmo turno. A 2ª mensagem continua no vendas (handoff persistido).
- [x] Confirmar que **não** sai `["` cru nem `[]` (bugs 1.1/1.3 corrigidos). ✅ 2026-07-02
- [x] Confirmar que **não** há ping-pong descontrolado (para por `MAX_ROUTING_DEPTH`/ciclo).
      ✅ hub-and-spoke: `maestro`/`zaad` só voltam pro Jarvis; bônus — escalação p/ humano
      só pelo Jarvis validada (zaad→default→`transfer_to_human`).
- [ ] **Allowlist:** instruir a triagem a transferir pra `financeiro` (fora de
  `routing_targets`) → a tool deve recusar: "não está entre os destinos permitidos".

## Teste 2 — Dependências (`requires_prior_call`) — parcial 2026-07-02 (ver QA)
- [x] `hooks_config` no Jarvis: `{"set_custom_attribute": {"requires_prior_call": "save_contact_info"}}`.
- [x] Forçar o LLM a tentar `set_custom_attribute(cpf)` antes de `save_contact_info`.
  - ✅ **Confirmado:** bloqueio pré-dispatch → LLM chamou `save_contact_info` → então
    `set_custom_attribute` passou (exec 361).
  - ⚠️ **Achado 2.A:** `transfer_to_human` espúrio (priming da conversa reativada pós-handoff
    humano) desligou a IA e engoliu a 2ª mensagem. #31 foi limpa p/ re-rodar. Ver QA.
- [x] ⚠️ **Reset (2b) ✅ CONFIRMADO** (contexto limpo): na msg "empresa é Techify" o
  `set_custom_attribute` **bloqueou de novo** (1º card sem execução) → `save_contact_info`
  → passou. O `executed` reseta por mensagem. Sem `transfer_to_human` espúrio desta vez
  (confirma que o Achado 2.A era priming). Limitação p/ multi-turno documentada no QA.

## Teste 3 — Trava de loop / limite de tool — parcial 2026-07-02 (ver QA)
- [x] `hooks_config`: `{"reminder_create": {"call_limit": 2}}`. Pedido "cria 4 lembretes".
  - ✅ **Confirmado:** `reminder_create` executou 2× e a 3ª/4ª bloquearam (exec 365).
  - ⚠️ **Achado 3.A (CORRIGIDO):** o texto do bloqueio dizia "nesta conversa" (escopo é
    por MENSAGEM) → o modelo aconselhou "crie uma nova conversa", o que é errado. Texto
    ajustado em [ai_engine/hooks.py](../ai_engine/hooks.py) → "nesta mensagem…". Precisa
    reiniciar o worker (`ai_engine` fora do reload) p/ re-testar o novo texto.
- [x] ⚠️ **Loop real ✅ CONFIRMADO — RISCO REAL** (plugin QA `loop_probe`, exec 368):
  `loop_probe_ping` (sempre "chame de novo", sem `call_limit`) executou **12×** e o modelo
  **nunca desistiu sozinho** — só parou na trava artificial da tool ("CONCLUÍDO" em 12).
  **56.974 tokens / US$0,025 numa mensagem** (~15–35× o normal).
  - **Código:** `agno Agent.tool_call_limit` default `None` e o WhatsBot não o seta →
    **sem teto de tool-calls no motor** (`MAX_ROUTING_DEPTH=5` é só p/ hops de agente).
  - ✅ **Correção IMPLEMENTADA + validada:** `tool_call_limit=_resolve_tool_call_limit()`
    no `_build_single_agent` ([agno_engine.py](../agent/agno_engine.py)); default 25, env
    `WHATSBOT_TOOL_CALL_LIMIT`. Revalidado com teto=5 → loop cortou em 5× (exec 371).
    Plugin de teste desabilitado após o uso.

## Teste 4 — Retorno ao triagem ✅ (feito 2026-07-02 — ver QA)
- [x] Após handoff pra Maestro, **resolver/fechar** a conversa no painel.
  - ✅ **Confirmado:** `set_status("closed")` zerou `active_agent_key` (+ `assignee`).
    Msg nova reabriu e caiu no **Jarvis (triagem)** (exec 367, `agent_key='default'`).
- [x] **Sem fechar:** o especialista chamar `transferir_agente(default)` de volta — já
  demonstrado no Teste 1 (hub-and-spoke: zaad→default). Não re-testado isolado.
- [x] ⚠️ **Gap documentado:** não existe retorno automático (inatividade / fim de assunto).
  A conversa gruda no especializado até fechar OU ele transferir de volta. Ver QA + Sugestões.

---

## Sugestões de melhoria (anotar ao final)
- **Escopo de `requires_prior_call`/`call_limit` por CONVERSA, não por mensagem** — o
  reset por mensagem quebra fluxos multi-turno (ex.: exigir autenticação uma vez só).
- **Guarda global de iterações de tool-call por agente** ✅ IMPLEMENTADO (Teste 3b):
  `tool_call_limit` no `Agent` ([agno_engine.py](../agent/agno_engine.py) — default 25,
  env `WHATSBOT_TOOL_CALL_LIMIT`). Antes NÃO existia (`agno` default `None`); só havia o
  cap de **hops** de agente (`MAX_ROUTING_DEPTH`). Validado: com teto=5 o loop cortou em 5.
- **Retorno automático ao triagem** (por config do agente: "após N min ocioso" ou
  "quando resolver o assunto").
- **Config saudável de agentes** para evitar bouncing: só a triagem como router;
  especialistas como folhas; prompts que respondem em vez de transferir.
- Revisar `transfer_to_human` sendo chamado com facilidade pelo `zaad` (desativa IA +
  alerta sonoro) — pode ser prompt/gatilho cedo demais.

## Pergunta em aberto para amanhã
Executar o roteiro eu mesmo (preciso do WhatsApp conectado no GOWA + número de teste)
OU o usuário roda e eu verifico logs/DB e monto o relatório de gaps + sugestões.
