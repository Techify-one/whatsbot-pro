# QA — Motor de Agentes (handoff, dependências, loops)

Registro de testes manuais do motor de agentes de IA (AGNO), com os problemas
encontrados e as correções aplicadas. Cada entrada segue o formato:
**o que foi testado → problema observado → correção feita.**

- **Data de início:** 2026-07-01
- **Ambiente:** dev nativo (`./linux_start.sh`), backend Postgres, canal GOWA (WhatsApp real)
- **Setup de agentes:** `default` (router → `zaad`, `maestro`), `zaad`, `maestro` — todos com `is_router=1` e allowlists mútuas. Conversa de teste: contato "~Whatsbot" (id 8), conversa 7, inbox "teste" (canal `gowa_MdPAavUPkc`).

---

## Pré-condição encontrada (não é bug)

**Sintoma:** "nada acontecia" — a IA não respondia a nenhuma mensagem.

**Causa:** o interruptor **global** da IA (`config.auto_reply`) estava `false`. Esse
gate é checado ANTES de tudo (global → canal → conversa); com ele OFF, nenhuma
conversa é respondida por mais que canal/conversa/contato estejam habilitados.

**Ação:** ligar o interruptor global no painel (Configurações → interruptor global da IA).
Depois disso o fluxo passou a rodar. Fica o registro para diagnóstico futuro.

---

## Teste 1 — Transição (handoff) entre agentes

**Cenário:** enviar "Quero comprar plano premium"; o `default` (triagem/router)
deve transferir para um agente de vendas via a tool `transferir_agente`.

**Resultado geral:** ✅ o handoff funciona. O `default` chamou
`transferir_agente(maestro)`, a transferência foi registrada (`active_agent_key`
gravado na conversa) e as mensagens seguintes passaram a ser atendidas pelo
agente de destino. A resposta multi-parte do agente de destino saiu corretamente
dividida (`split_messages`).

Durante o teste surgiram **dois problemas**, ambos corrigidos:

### Problema 1.1 — JSON de `split_messages` truncado vazando como texto cru

**Observado:** numa resposta gerada no MESMO turno de uma chamada de tool
(`transferir_agente`), a mensagem enviada ao cliente saiu como JSON cru e cortado:
`["Excelente! 🎉 Que bom que você quer dar esse` (sem fechar o `]`).

**Causa:** o modelo começou a escrever um "preâmbulo" já no formato de split
(`["...`) junto com a tool call e cortou no meio para emitir a tool. Esse texto
truncado escapou como resposta final. Como o array JSON está incompleto,
`parse_split_reply` ([server/helpers.py](../server/helpers.py)) falhava no
`json.loads` e caía no fallback `return [reply]`, enviando o `["` cru ao usuário.

**Correção:** adicionado `_salvage_split_array()` em
[server/helpers.py](../server/helpers.py). Quando o texto começa com `["` e o
`json.loads` falha, ele recupera o conteúdo legível das strings (inclusive uma
string final não terminada, o caso do truncamento) em vez de vazar o markup JSON.
`parse_split_reply` agora chama esse salvamento antes do fallback. Casos plain e
`[texto]` que não são split continuam intactos.
- Verificado: `'["Excelente! ... esse'` → `['Excelente! ... esse']`; `'["a", "b'`
  → `['a', 'b']`; `'[importante] veja'` → inalterado.

### Problema 1.2 — Ping-pong / loop de transferência entre agentes no mesmo turno

**Observado:** ao perguntar "qual preço?", o agente ativo (`maestro`) chamou
`transferir_agente` **duas vezes na mesma mensagem**: `maestro → zaad` e depois
`zaad → maestro`, terminando onde começou. Respostas confusas ("Você está falando
agora com o Maestro..." logo após pular para outro agente e voltar).

**Causa:** não havia trava contra múltiplas transferências por turno. O
`hooks_config` de todos os agentes estava vazio (`{}`), então nada limitava a tool
`transferir_agente`. Como o handoff é per-mensagem (só rebinda `active_agent_key`
para a PRÓXIMA mensagem), transferir mais de uma vez no mesmo turno só embaralha o
ponteiro; e as allowlists mútuas (`maestro ↔ zaad`) permitem o ciclo A→B→A.

**Correção (parcial) + CORREÇÃO DE ANÁLISE:** adicionado guard em
[ai_engine/hooks.py](../ai_engine/hooks.py): `_DEFAULT_HOOKS =
{"transferir_agente": {"call_limit": 1}}` (mesclado com o `hooks_config` do agente;
o do agente vence). Isso impede um agente de chamar `transferir_agente` 2× **no
mesmo hop** — hardening menor.

⚠️ **Mas essa NÃO é a trava de loop principal, e minha análise inicial estava
errada** (dizia "não há trava de loop"). A verdade, confirmada nas `execution_steps`:
existe **roteamento multi-hop DENTRO do mesmo turno** ([ai_engine/routing.py](../ai_engine/routing.py)):
quando um agente transfere, o agente de destino já responde a MESMA mensagem
(`run_with_routing`). E esse loop **já tem trava**:
- `MAX_ROUTING_DEPTH = 5` — no máx. 5 execuções de agente por mensagem.
- **Detecção de ciclo**: `if nxt in seen: break` — impede A→B→A.

Ou seja, o "ping-pong" observado (maestro→zaad→default) **parou sozinho** ao tentar
voltar a um agente já visto; não é loop infinito. Como cada hop tem sua própria
lista `executed`, o `call_limit:1` reseta a cada hop e **não** limita o número de
hops — quem faz isso é o `MAX_ROUTING_DEPTH`.

**A verdadeira causa da "estranheza"** é de **configuração/prompt**, não do motor:
os 3 agentes estão como `is_router=1` apontando uns pros outros (allowlists mútuas)
e os prompts mandam transferir com facilidade; o `zaad` ainda chama
`transfer_to_human` (que desativa a IA e dispara o alerta sonoro). Recomendação:
só a `triagem` deveria ser router; `vendas`/`suporte` como folhas (`is_router=0` ou
`routing_targets` só de volta pra triagem) e prompts que RESPONDEM em vez de
transferir. (Ver "Sugestões de melhoria".)

### Problema 1.3 — Resposta `[]` (array de split vazio) vazando como mensagem

**Observado:** apareceu uma mensagem literal `[]` no chat (msg 1048, assistant),
inclusive enviada ao WhatsApp.

**Causa:** num hop de pura-transferência o agente não tem texto pro usuário e emite
um array de split vazio (`[]`). `parse_split_reply("[]")`
([server/helpers.py](../server/helpers.py)) fazia `json.loads` com sucesso (`[]`),
mas como o array filtrado ficava vazio, caía no fallback `return [reply]` e enviava/
salvava o `"[]"` cru (o envio salva cada parte individualmente em
[app/services/messaging_service.py](../app/services/messaging_service.py)).

**Correção:** `parse_split_reply` agora, quando o JSON é um array de strings válido,
retorna suas partes não-vazias **mesmo que isso dê `[]`**. Um array vazio/whitespace
vira `[]` → o envio é abortado (`if not parts: return`) e nada é salvo. Não vaza
mais `"[]"`.
- Verificado: `'[]'` → `[]`; `'[""]'` → `[]`; `'[" "]'` → `[]`; `'["a","b"]'` →
  `['a','b']`; `'["Oi"]'` → `['Oi']`.

---

## Pendentes

- **Teste 2 — Dependências (`requires_prior_call`):** validar que uma tool só roda
  depois de outra (ex.: `buscar_pedido` exige `autenticar`) e documentar que o
  escopo é por mensagem.
- **Teste 3 — `call_limit` / loop de tool genérica:** validar limite por tool e o
  comportamento anti-loop do AGNO (não há limite global de iterações no código do
  WhatsBot).
- **Teste 4 — Retorno ao agente de triagem:** hoje NÃO é automático — a conversa
  fica presa no agente especializado até ser fechada (`set_status("closed")` zera
  `active_agent_key`) ou até ele chamar `transferir_agente(triagem)` de volta.
