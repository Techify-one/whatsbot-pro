# QA — Motor de Agentes (handoff, dependências, loops)

Registro de testes manuais do motor de agentes de IA (AGNO), com os problemas
encontrados e as correções aplicadas. Cada entrada segue o formato:
**o que foi testado → problema observado → correção feita.**

- **Data de início:** 2026-07-01
- **Ambiente:** dev nativo (`./linux_start.sh`), backend Postgres, canal GOWA (WhatsApp real)
- **Setup de agentes (ORIGINAL, Teste 1):** `default` (router → `zaad`, `maestro`), `zaad`, `maestro` — todos com `is_router=1` e allowlists mútuas. Conversa de teste: contato "~Whatsbot" (id 8), conversa 7, inbox "teste" (canal `gowa_MdPAavUPkc`).
- **Setup de agentes (ATUAL, a partir de 2026-07-02 — ver "Reconfiguração" abaixo):** só o `default` (Jarvis) roteia livremente; `maestro`/`zaad` são folhas que só voltam pro Jarvis e não têm `transfer_to_human`.

---

## Reconfiguração de topologia + limpeza do banco (2026-07-02)

Antes de retomar (Teste 2 em diante), a config dos agentes foi ajustada para a
topologia que o usuário definiu, eliminando o bouncing observado no Teste 1:

- **Jarvis (`default`)** — principal/triagem. `is_router=1`, `routing_targets=["maestro","zaad"]`,
  `tool_names=None` (todas as tools, **inclui** `transfer_to_human`). É o ÚNICO que
  escala para humano. Prompt: adicionado o papel de decidir encerrar ou chamar humano.
- **Maestro (`maestro`)** e **Zaad (`zaad`)** — especialistas folha. `routing_targets=["default"]`
  (só voltam pro Jarvis, nunca a outro especialista), `tool_names=["save_contact_info",
  "set_custom_attribute","transferir_agente","reminder_create"]` (**sem** `transfer_to_human`).
  Prompt reescrito: quando terminam ou saem do escopo → sempre `transferir_agente(default)`;
  nunca transferem para outro especialista nem para humano (quem decide isso é o Jarvis).
- As edições foram via `agent_repo.save` (versionadas + snapshot em `ai_agents_history` →
  reversíveis). Versões: `default` v3, `maestro` v2, `zaad` v2.

**Limpeza do banco (slate limpo p/ observar só os testes):** apagadas todas as
mensagens (969), conversas (27), execuções (200) + `execution_steps` (523),
`unread_msg_ids`, `conversation_label_links`; contadores de não-lido dos contatos
zerados. Preservados: 26 contatos, tags, agentes, canais, config. Backup JSON das
tabelas afetadas em `scratchpad/backup_before_cleanup.json`. (O usuário já havia
limpado as conversas do lado do WhatsApp e desconectado os 2 canais GOWA.)

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

## Teste 1 — REVALIDAÇÃO com a topologia nova (2026-07-02) ✅

Conversa #31 (contato Ezequiel, `556490000001`), 3 turnos numa tacada. Confirmado
por `messages` + `executions.routing_steps`:

| Turno (msg do cliente) | Agente inicial | `routing_steps` | Agente final | OK? |
|---|---|---|---|---|
| "Quero comprar o plano premium" | default | `default→maestro` | maestro | Handoff limpo, 1 hop |
| "qual valor?" | maestro | `maestro→default`, `default→zaad` | zaad | Multi-hop 2 saltos, terminou no Zaad, **sem ping-pong** |
| "quero falar com um humano" | zaad | `zaad→default` (+ `transfer_to_human` dentro do hop do Jarvis) | default | Zaad devolveu ao Jarvis; **só o Jarvis** escalou p/ humano |

**Resultados:**
- ✅ Handoff funciona e o destino responde no MESMO turno (multi-hop).
- ✅ **Sem ping-pong / sem loop entre especialistas** — como `maestro`/`zaad` só têm
  `routing_targets=["default"]`, qualquer desvio passa pelo Jarvis (hub-and-spoke).
- ✅ **Escalação p/ humano centralizada no Jarvis**: o Zaad (sem `transfer_to_human`)
  não pôde escalar direto; devolveu ao Jarvis (`transferir_agente(default)`) e o Jarvis
  chamou `transfer_to_human`. Conversa terminou `active_agent_key=NULL`, `ai_active=0`
  (a tool desativa a IA e desatribui) — correto.
- ✅ **Bugs 1.1 / 1.3 não reapareceram**: nenhum `["` cru nem `[]` vazado; `split_messages`
  saiu em partes limpas.

**Observação (ajuste de prompt, não é bug):** no turno "qual valor?", o **Maestro**
(vendedor) tratou preço como "fora da alçada de vendas" e devolveu ao Jarvis, que
mandou pro Zaad. Funcionou, mas gera um hop extra. Decidir a quem pertence
"preço/planos" (Maestro vs. Zaad) e reforçar no prompt evita o salto — hoje o produto
cobra por créditos e o Zaad explicou bem, então é defensável manter no Zaad.

---

## Teste 2 — Dependências (`requires_prior_call`) (2026-07-02)

**Setup:** no Jarvis (`default`), `hooks_config =
{"set_custom_attribute": {"requires_prior_call": "save_contact_info"}}`. Atributo
`cpf` existe em `custom_attribute_definitions`. Conversa #31 reativada.

**Mensagem:** "Meu CPF é 111.222.333-44". Ordem real das tool calls (exec 361,
`execution_steps` + `messages`):

1. `set_custom_attribute(cpf)` → **BLOQUEADO** pelo hook (sem `tool_executed`; só o
   card de tool_call sem seta de resultado). ✅ dependência barrou.
2. `transfer_to_human` → executou (espúrio — ver achado abaixo).
3. `save_contact_info(observation="CPF atualizado…")` → executou (recuperação).
4. `set_custom_attribute(cpf, scope=contact)` → **liberado**, executou → "✅ CPF salvo".

**Resultado:** ✅ **Mecanismo confirmado** — `set_custom_attribute` só passou DEPOIS
de `save_contact_info` rodar no mesmo turno. O bloqueio é pré-dispatch (retorna a
string de bloqueio ao LLM, não gera `tool_executed`), mas gera o card de tool_call.

### Achado 2.A — `transfer_to_human` espúrio ao reativar conversa pós-handoff humano

**Observado:** processando "Meu CPF é…", o Jarvis chamou `transfer_to_human`
(`reason="cliente pediu atendente humano"`) sem o cliente ter pedido. Efeito
colateral: `transfer_to_human` **desliga a IA** (`ai_active=0`), então a mensagem
seguinte ("Ah, e minha empresa é a Techify", exec 362) parou em `batch_accumulated`
— o LLM nem foi chamado (gate da IA off). Por isso "não anotou a empresa".

**Causa:** artefato de teste + priming de contexto. A conversa #31 tinha ACABADO de
fazer um handoff pra humano no Teste 1 (as últimas msgs do contexto eram
"quero falar com um humano" → `transfer_to_human` → "Já estou encaminhando você").
Ao reativar a IA no meio desse handoff (`ai_active=1`) sem limpar o contexto, o
modelo (deepseek-v4-pro, `context_messages=6`) viu o padrão e repetiu o
`transfer_to_human`. Num fluxo real isso não ocorre: após `transfer_to_human` a IA
fica desligada e um humano assume — foi a reativação manual que criou o estado
contraditório.

**Encaminhamento:** para o 2b, o histórico da #31 foi **limpo** e a IA reativada
(contexto fresco). Fica a recomendação: **ao reativar a IA numa conversa que estava
em atendimento humano, resetar/anotar o contexto** (ou ao menos não herdar as msgs
de transferência), senão o modelo tende a re-disparar `transfer_to_human`.

### 2b — reset por mensagem ✅ CONFIRMADO (re-rodado no contexto limpo)

Após limpar o histórico da #31 (removendo o priming de "humano"), duas mensagens:

- **"Meu CPF é 111.222.333-44"** (exec 363): `tool_executed=[]` — o modelo só confirmou
  ("Seu CPF já está registrado aqui…"), pois o CPF já fora salvo antes. **Nenhum
  `transfer_to_human`** desta vez → comprova que o Achado 2.A era priming de contexto,
  não um problema do motor.
- **"Ah, e minha empresa é a Techify"** (exec 364): 3 cards de tool_call, mas só 2
  `tool_executed` (`save_contact_info`, `set_custom_attribute`). O 1º
  `set_custom_attribute(company)` foi **BLOQUEADO** (não virou `tool_executed`) →
  `save_contact_info` → `set_custom_attribute(company)` passou → "✅ Empresa salvo".

**Conclusão:** o escopo de `requires_prior_call` é **por-mensagem** — o
`save_contact_info` da mensagem anterior NÃO conta; cada mensagem re-exige o
pré-requisito. Confirmado que isso é uma limitação para fluxos de autenticação
multi-turno (ver "Sugestões de melhoria": escopo por CONVERSA). O modelo se recupera
sozinho do bloqueio (chama o pré-requisito e re-tenta), então o efeito prático é
apenas uma tool-call extra por mensagem, não um travamento.

---

## Teste 3 — `call_limit` / trava de loop de tool (2026-07-02)

**Setup:** Jarvis (`default`) com `hooks_config = {"reminder_create": {"call_limit": 2}}`.
**Mensagem:** "Me cria 4 lembretes separados: 1) ligar pro João…, 2) pagar a conta de
luz, 3) comprar café, 4) revisar o relatório de vendas".

**Resultado:** ✅ **`call_limit` funciona.** O `reminder_create` executou **exatamente
2×** (exec 365, `tool_executed` = João + luz; `plugin_lembretes_items` tem só 2 linhas).
A 3ª e 4ª chamadas (café, relatório) foram **bloqueadas** (cards de tool_call sem seta
de resultado, nenhum `tool_executed`). O modelo se recuperou e explicou ao cliente que
só criou os 2 primeiros.

### Achado 3.A — mensagem de bloqueio dizia "nesta conversa" (escopo é por MENSAGEM) → CORRIGIDO

**Observado:** o Jarvis respondeu *"atingi o limite de lembretes **por conversa**…
sugiro criar em **uma nova conversa**"*. Conselho **errado**: o `call_limit` é
**por-mensagem** (o `executed` reseta a cada mensagem, como o Teste 2), então bastava o
cliente mandar OUTRA mensagem na MESMA conversa. Não precisa de conversa nova.

**Causa:** o texto do bloqueio em [ai_engine/hooks.py](../ai_engine/hooks.py) dizia
`"… {limit} chamada(s) nesta conversa. Não a chame de novo."` — "nesta conversa"
contradiz a própria semântica (o docstring do arquivo diz "at most N times **per
message**"). O modelo repassou o wording errado ao cliente.

**Correção:** texto ajustado para
`"… {limit} chamada(s) nesta mensagem. Não a chame de novo agora — o limite é por
mensagem e reseta na próxima mensagem do cliente."` Unit tests (`tests/test_hooks.py`,
15/15) continuam passando (checam presença de bloqueio, não a string).
⚠️ **`ai_engine` não está nos `--reload-dir`** → a correção só entra em vigor após
reiniciar o worker; re-testar depois do restart para ver o novo texto.

**Nota de design (já em "Sugestões"):** se o produto quiser um limite que valha para a
CONVERSA inteira (ex.: "no máx. 3 lembretes por cliente"), isso é uma feature à parte —
o `call_limit` atual é anti-spam/anti-loop DENTRO de um turno.

### 3b — loop real sem `call_limit` ✅ CONFIRMADO (2026-07-02) — RISCO REAL

**Inspeção de código (resposta direta):** `agno.agent.Agent.tool_call_limit` tem default
`None` (`venv/.../agno/agent/agent.py:177`), e o WhatsBot **não o seta** em lugar nenhum
([agent/agno_engine.py](../agent/agno_engine.py) `_build_single_agent`, `_CONTEXT_OFF`) —
logo **não há teto de iterações de tool-call no motor**. A única trava é
`MAX_ROUTING_DEPTH=5`, que limita **hops de AGENTE** (handoffs), não tool-calls dentro de
um agente.

**Teste ao vivo:** plugin de QA `loop_probe` com a tool `loop_probe_ping` que sempre
responde "ainda NÃO terminou, chame de novo" (sem `call_limit`), com trava de segurança
em `MAX_CALLS=12`. Mensagem: "roda o loop de teste completo…".

**Resultado (exec 368):**
- `loop_probe_ping` executou **12×** — o modelo (deepseek-v4-pro) chamou obedientemente
  passo a passo e **só parou quando a tool devolveu "CONCLUÍDO"** (na trava artificial).
  **Não houve auto-desistência** em nenhuma das 11 iterações "não terminou".
- **Custo de UMA mensagem: 56.974 tokens / US$0,025** — ~15–35× uma mensagem normal
  (~3,5k tokens / US$0,0015). Escala linear com as iterações; sem a trava artificial,
  seguiria enquanto o modelo obedecesse.

**Conclusão / RISCO:** uma tool (de plugin) com bug/malícia que sempre peça "chame de
novo" **loopa sem freio do motor**, limitada só pela paciência do modelo — que aqui se
mostrou alta (≥12). É um vetor real de estouro de crédito/latência.

**Correção IMPLEMENTADA + VALIDADA (2026-07-02):** [agent/agno_engine.py](../agent/agno_engine.py)
agora passa `tool_call_limit=_resolve_tool_call_limit()` no `_build_single_agent`
(`DEFAULT_TOOL_CALL_LIMIT = 25`, override por env `WHATSBOT_TOOL_CALL_LIMIT`; `0`/negativo
desabilita; valor inválido cai no default = fail-safe). O `_build_followup_agent` é
`tools=None` (não faz loop), então não precisa. Semântica do AGNO ao exceder: **não**
levanta — injeta "Tool call limit reached" para as chamadas extras e o run termina
gracioso (`agno/models/base.py:2327`).

Revalidação ao vivo (teto rebaixado p/ 5 no demo):
- 1ª tentativa (teto 25, tool `MAX_CALLS=40`): o modelo parou **sozinho no passo 12**
  (narrou "Passo 12 concluído" — alucinação) antes de bater no teto de 25. Ou seja: a
  paciência natural do modelo (~12) é menor que 25, então o backstop não disparou. Também
  reapareceu o efeito de **contexto poluído** (Achado 2.A): com o histórico do loop
  anterior (incluindo um "loop_probe_ping" escrito como texto), a run seguinte só narrou e
  **nem chamou a tool** (0×). Limpar o histórico da conversa resolveu.
- 2ª tentativa (teto **5**, contexto limpo): `loop_probe_ping` executou **exatamente 5×**
  (exec 371), o AGNO cortou a 6ª e o modelo **parou e explicou** ("limite de chamadas da
  ferramenta foi atingido antes da conclusão"). Custo contido: 25k tokens / US$0,011.
  ✅ **Backstop confirmado.**

Teto revertido para 25 e plugin `loop_probe` desabilitado após a validação (não deixar em
produção). **Aprendizado de método:** re-testar loops na MESMA conversa acumula contexto
que enviesa o modelo — limpar o histórico entre runs (ou usar conversa nova).

---

## Teste 4 — Retorno ao triagem (2026-07-02) ✅

Fluxo em 3 passos na conversa #31 (contato Ezequiel), confirmado no DB a cada passo:

1. **Handoff pra especialista** — "quero comprar o plano premium" → Jarvis
   `transferir_agente(maestro)`. `active_agent_key='maestro'` (exec 366, routing
   `default→maestro`). Conversa "presa" no Maestro.
2. **Resolver (fechar) no painel** — botão *Resolver* (formulário pediu o atributo de
   conversa `resultado`, obrigatório; preenchido `obs/motivo/resultado`). Efeito de
   `set_status("closed")` ([conversation_repo.py](../db/repositories/conversation_repo.py)):
   `status=closed`, **`active_agent_key=None`** (zerado), `assignee_user_id=None`,
   `resolved_at` carimbado. `ai_active` permanece `1` (fechar NÃO desliga a IA).
3. **Mensagem nova** — "oi boa tarde" → conversa **reabre** automaticamente
   (`conversation_event` "reaberta"); como `active_agent_key` estava `None`, a resolução
   caiu no default (inbox `default_agent_key=None` → agente global `default`). O **Jarvis
   (triagem)** respondeu a saudação (exec 367, `agent_key='default'`, sem routing), e o
   `active_agent_key` foi re-gravado como `default`. Header do painel mostrou "Jarvis
   (Anfitrião)".

**Resultado:** ✅ o retorno ao triagem **via fechamento** funciona como o checklist previa.

**Gap confirmado (documentado, não é bug):** o retorno ao triagem **não é automático**.
Só acontece se (a) alguém **fechar** a conversa (zera o agente) ou (b) o especialista
chamar `transferir_agente(default)` de volta (já visto no Teste 1, hub-and-spoke). Não
há retorno por inatividade / "fim de assunto". Enquanto a conversa fica aberta e ninguém
transfere de volta, ela gruda no especialista. Ver "Sugestões de melhoria".

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
