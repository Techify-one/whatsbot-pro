# WhatsBot — Documentação para análises de dados por IA

Este conjunto documenta o banco de dados do WhatsBot **para uma IA analista** (interna ou externa) rodar análises e alimentar relatórios: atendimentos e protocolos por atendente/geral, recorte IA×humano, tempos de resposta, diligência dos vendedores, padrões de conversão etc. O público é um **LLM** (e o dev). Escreva/leia como contexto: direto, com o nome real das tabelas/colunas e SQL Postgres pronto.

> **Proveniência.** Gerado a partir de um recon do código na branch `developer` em **2026-07-17**, com citações `arquivo:linha`. O código evolui — ao usar em produção, confira contra o schema atual (`db/tables.py` + migrations). Nada de instrumentação foi implementado; o escopo atual é **só documentação**.

---

## Como ler (ordem sugerida)

| # | Documento | O que é | Quando usar |
|---|---|---|---|
| 1 | [01-modelo-de-dados.md](01-modelo-de-dados.md) | **Dicionário de dados** — glossário, mapa de junções e cada tabela relevante (core + `plugin_protocolos_*`) com colunas/significado | Para saber **o que existe** e **como as tabelas se ligam** |
| 2 | [02-regras-de-negocio-e-pegadinhas.md](02-regras-de-negocio-e-pegadinhas.md) | **Semântica e armadilhas** — status vs arquivado, volatilidade de `resolved_at`/`assignee`, quem é IA/humano/echo, trilha `conversation_event`, TZ, etc. | **Leia antes de escrever qualquer query** — é o que evita conta errada |
| 3 | [03-cookbook-de-analises.md](03-cookbook-de-analises.md) | **Receitas** — cada relatório pedido com selo PRONTO/PARCIAL/BLOQUEADO, SQL executável e ressalvas | Para **copiar a query** de uma análise específica |
| 4 | [04-instrumentacao-recomendada.md](04-instrumentacao-recomendada.md) | **Cardápio de melhorias no schema** (não implementadas) que transformam PARCIAL/BLOQUEADO em caminho limpo | Ao decidir **investir em instrumentação** depois |
| 5 | [05-arquitetura-plugin-analises.md](05-arquitetura-plugin-analises.md) | **Proposta** do plugin `analises` reusando o motor agêntico do `melhorias` (a discutir) | Ao decidir **construir o plugin** de análises |

Um agente analista deve receber **01+02+03** como conhecimento de sistema (dicionário + regras + cookbook); **04** vira backlog e **05** é o desenho do produto.

---

## Cartão de referência rápida (o que mais se erra)

### Glossário
- **Atendimento** = uma conversa (tabela `atendimentos`, renomeada de `conversations`). Unidade de "atendimentos abertos/fechados". Alias Python `conversations = atendimentos` (`db/tables.py:853`) — repos dizem `conversations.c.*` mas gravam em `atendimentos`.
- **Conversa nativa** = o mesmo objeto `atendimentos` (sem o plugin de protocolos).
- **Protocolo** = ticket do **plugin** `protocolos` (`plugin_protocolos_protocolos`), por-conversa, **distinto** do atendimento. ⚠️ `plugin_protocolos_atendimentos` é um **falso amigo** (tabela de vínculo/ciclo — N linhas por conversa), **não** a lista core.
- **Contato** = linha em `contacts` (número/pessoa; funde canais).
- **Atendente** = linha em `users` (role `atendente|gestor|admin` via `user_roles`→`roles.key`).
- **IA / agente** = identificado por `messages.agent_key` (não-nulo); rastreado em `executions`.

### Identidade do remetente de uma mensagem (discriminadores canônicos — tabela `messages`)
| Quem | Condição |
|---|---|
| **Cliente** | `role='user'` |
| **IA** | `role='assistant' AND agent_key IS NOT NULL` (tipicamente `status='sent'`, `execution_id` setado) |
| **Atendente humano** (painel) | `role='assistant' AND status='operator' AND sent_by_user_id IS NOT NULL` |
| **Echo** (celular do operador) | `role='assistant' AND status='operator' AND sent_by_user_id IS NULL AND agent_key IS NULL` |
| **Card painel-only** (não é WhatsApp) | `role IN ('tool_call','system_notice','transcription','private_note','error','conversation_event','system')` |

Não existe coluna `direction`/`source` em `messages`; o enum `source` (ai/operator/private_ai/…) é só do event-bus. `ts` (epoch float UTC) é o único timestamp por mensagem.

### Decisões canônicas (valem em todas as docs)
- **Timezone**: epoch float em UTC. "No dia"/"hoje" **sempre** com `(to_timestamp(ts) AT TIME ZONE 'America/Sao_Paulo')::date`.
- **IA vs humano** (por atendimento): derive de `messages` — `tem_ia` = existe assistant com `agent_key`; `tem_humano` = existe assistant `status='operator' AND sent_by_user_id NOT NULL`. Classes: IA-only / Humano-only / **Misto** / Sem resposta. `executions.has_ai` é só complemento (podado, `conversation_id` nullable em linhas legadas).
- **Iniciado por atendente**: `atendimentos.origin IN ('outbound','manual')` **E** a 1ª mensagem `role='assistant'` tem `sent_by_user_id NOT NULL` (isola humano da IA). Regra de re-engajamento ("novo contato" = ≥ **N** dias sem inbound; N=30 default, 15 configurável) é **calculada**, não existe no banco.
- **Conversão / "venda"**: **não existe coluna de resultado hoje**. Convenção recomendada (a firmar): etiqueta de conversa `venda` (`atendimento_labels`) + atributos `produto`/`valor`. Análise de "estratégia que converte" fica PARCIAL/BLOQUEADA até haver esse sinal.
- **Acesso da IA**: plugin agêntico **interno** — lê **todas** as tabelas (SELECT), escreve **só** nas próprias (`plugin_analises_*`). Escrita de volta no core, se um dia, **via API REST**, nunca SQL cru (repos aplicam `display_id`/índice único/`conversation_event`/broadcasts/RBAC).

---

## O que NÃO dá pra medir de forma limpa hoje (resumo dos buracos)

| Buraco | Efeito | Onde resolver |
|---|---|---|
| **Sem `closed_by_user_id`** — fechar zera `assignee_user_id` (`conversation_repo.py:641`) e `resolved_at` é volátil (reabrir apaga) | "Atendimentos fechados **por atendente**" no core é **BLOQUEADO** (só via texto do card `conversation_event`) | [04](04-instrumentacao-recomendada.md) §1–§2 |
| **Sem timestamp de 1ª resposta** — `waiting_since` é coluna morta | Tempo de resposta é minerado de `messages.ts`; latência de entrega/leitura é irrecuperável | [04](04-instrumentacao-recomendada.md) §3 |
| **Split IA×humano sem campo autoritativo** | Convenção heurística (ver cartão acima) | [03](03-cookbook-de-analises.md) C |
| **Iniciado-por-atendente é grosseiro** (`origin` funde operador+IA) + regra 15/30d não existe | Query calculada obrigatória | [03](03-cookbook-de-analises.md) F, [04](04-instrumentacao-recomendada.md) §5 |
| **Sem sinal de conversão** | "Estratégias que convertem" sem variável-alvo | Convenção `venda` + [04](04-instrumentacao-recomendada.md) §4 |
| **`usage` sem `conversation_id`/`agent_key`** (só `contact_id`) | Custo/token por agente vem de `executions` (perde breakdown por modelo) | [04](04-instrumentacao-recomendada.md) §6 |

---

## Contexto: relatórios externos no Telegram (fora de escopo implementar)

Automações externas empurrarão relatórios diários a grupos do Telegram — atendimentos abertos/fechados no dia (por atendente e geral), protocolos abertos/fechados (por atendente e geral), ambos com recorte IA, contagem de conversas nativas e novos contatos iniciados por atendente (regra 15/30 dias). As **consultas** dessas métricas estão no [cookbook (03)](03-cookbook-de-analises.md); construir as automações em si está fora do escopo desta documentação.
