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
- `auto_reply` global = **ON** (senão nada responde — é o 1º gate).
- Backend Postgres; canal GOWA (WhatsApp real). Conexão via `.env` → `DATABASE_URL`.
- Consultar DB: `set -a; source .env; set +a` e inicializar engine com `init_engine(os.environ["DATABASE_URL"])`.
- Correções já aplicadas (Teste 1): salvamento de JSON de split truncado e `[]` vazio
  ([server/helpers.py](../server/helpers.py)); `call_limit:1` default em `transferir_agente`
  ([ai_engine/hooks.py](../ai_engine/hooks.py)).
- **Dica de observabilidade:** dar a cada agente uma assinatura fixa no prompt
  ("AQUI É VENDAS") para ver a olho quem respondeu.

---

## Teste 1 — Handoff entre agentes ✅ (feito, revalidar após fixes)
- [ ] Enviar "quero comprar o plano premium".
  - **Esperado (corrigido):** a triagem chama `transferir_agente(vendas)` **E** o
    vendas já responde na MESMA mensagem (multi-hop). No DB: `active_agent_key='vendas'`,
    card `conversation.agent_changed`, `executions.routing_steps` registra o hop.
  - ⚠️ Ex-nota "só vale da próxima mensagem" estava **errada** — o destino responde no
    mesmo turno. A 2ª mensagem continua no vendas (handoff persistido).
- [ ] Confirmar que **não** sai `["` cru nem `[]` (bugs 1.1/1.3 corrigidos).
- [ ] Confirmar que **não** há ping-pong descontrolado (para por `MAX_ROUTING_DEPTH`/ciclo).
- [ ] **Allowlist:** instruir a triagem a transferir pra `financeiro` (fora de
  `routing_targets`) → a tool deve recusar: "não está entre os destinos permitidos".

## Teste 2 — Dependências (`requires_prior_call`)
- [ ] No agente, editar `hooks_config`: `{"buscar_pedido": {"requires_prior_call": "autenticar"}}`
  (usar nomes de tools que o agente realmente tenha).
- [ ] Forçar o LLM a tentar `buscar_pedido` antes de `autenticar`.
  - **Esperado:** bloqueio → "só pode ser usada depois de 'autenticar'" → o LLM chama
    `autenticar` e então `buscar_pedido` passa.
- [ ] ⚠️ **Testar o reset:** em nova mensagem da mesma conversa, `buscar_pedido` volta a
  exigir `autenticar` (estado `executed` reseta por mensagem). Documentar como
  limitação — provavelmente indesejado num fluxo de autenticação multi-turno.

## Teste 3 — Trava de loop / limite de tool
- [ ] `hooks_config`: `{"minha_tool": {"call_limit": 2}}`. Pedir "chame minha_tool 5 vezes".
  - **Esperado:** a partir da 3ª → "já atingiu o limite de 2 chamada(s)" (no mesmo turno).
- [ ] ⚠️ **Loop real:** criar uma tool que sempre devolva "chame de novo" **sem**
  `call_limit`; ver se o AGNO trava sozinho ou repete. Cronometrar e contar
  `Tool call for ...` no log.
  - **Documentar:** há trava de **hops de agente** (`MAX_ROUTING_DEPTH=5`), mas **não**
    há guarda global de **iterações de tool-call por agente** no código do WhatsBot —
    isso depende do comportamento interno do AGNO. Se o AGNO não proteger, é risco.

## Teste 4 — Retorno ao triagem (comportamento atual)
- [ ] Após handoff pra vendas, **resolver/fechar** a conversa no painel.
  - **Esperado:** `set_status("closed")` zera `active_agent_key`. Nova mensagem cai no
    default do inbox (triagem). Confirmar no DB.
- [ ] **Sem fechar:** único jeito de voltar hoje é o vendas chamar
  `transferir_agente(triagem)`. Testar com a instrução no prompt do vendas: "quando
  sair do assunto de vendas, transfira de volta para triagem".
- [ ] ⚠️ **Documentar o gap:** não existe retorno automático (por inatividade / fim de
  assunto). A conversa gruda no especializado até fechar ou ele transferir de volta.

---

## Sugestões de melhoria (anotar ao final)
- **Escopo de `requires_prior_call`/`call_limit` por CONVERSA, não por mensagem** — o
  reset por mensagem quebra fluxos multi-turno (ex.: exigir autenticação uma vez só).
- **Guarda global de iterações de tool-call por agente** (máx. N tool-calls por
  execução de agente) — hoje só há o cap de hops de agente (`MAX_ROUTING_DEPTH`).
- **Retorno automático ao triagem** (por config do agente: "após N min ocioso" ou
  "quando resolver o assunto").
- **Config saudável de agentes** para evitar bouncing: só a triagem como router;
  especialistas como folhas; prompts que respondem em vez de transferir.
- Revisar `transfer_to_human` sendo chamado com facilidade pelo `zaad` (desativa IA +
  alerta sonoro) — pode ser prompt/gatilho cedo demais.

## Pergunta em aberto para amanhã
Executar o roteiro eu mesmo (preciso do WhatsApp conectado no GOWA + número de teste)
OU o usuário roda e eu verifico logs/DB e monto o relatório de gaps + sugestões.
