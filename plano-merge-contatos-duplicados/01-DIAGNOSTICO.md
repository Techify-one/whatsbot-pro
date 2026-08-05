# Diagnóstico — contatos duplicados por variante BR

Investigação de **2026-08-04** no banco de produção `whatsbot`@10.8.100.5, inteira em
transação `READ ONLY`.

## 1. O caso reportado

| conv | contact | phone | nome | criado | última atividade |
|---|---|---|---|---|---|
| 7213 | 9468 | `556992577740` (12) | Adilson Lins | 2025-04-23 | **2026-08-04** |
| 12878 | 9546 | `5569992577740` (13) | Adilson **Club** | 2026-04-29 | 2026-07-15 |

Mesmo número (DDD 69), dois contatos, duas conversas abertas, ambas atribuídas ao
usuário 4, ambas no inbox 21 (`whatsapp_cloud_bc081279`). Por isso o template saiu duplicado.

## 2. Causa raiz — a regra do 9º dígito

Não é bug de importação. É a regra do WhatsApp brasileiro, visível no próprio banco:

| DDD | contatos com 12 dígitos | contatos com 13 dígitos |
|---|---|---|
| 11 (SP) | 17 | **1.257** |
| 21 (RJ) | 4 | **751** |
| 27 (ES) | 3 | **225** |
| 31 (MG) | **403** | 18 |
| 62 (GO) | **444** | 19 |
| 81 (PE) | **498** | 16 |
| 85 (CE) | **438** | 25 |

A Meta entrega **DDD 11–27 com o 9** e **DDD 31–99 sem o 9**. Os **203 pares são todos de
DDD ≥ 31** — nenhum de DDD ≤ 28. Ou seja: o lado de 13 dígitos é sempre o "extra",
criado por alguém que digitou o número no formato ANATEL.

Confirmação adicional: em todos os 203 pares o dígito inicial do número é **8 ou 9**
(móvel). Nenhum fixo entrou no conjunto — não há falso positivo por telefone fixo.

## 3. Quem cria o lado errado

O sufixo dos nomes do lado de 13 dígitos é um catálogo de campanhas:

> Adilson **Club** · Danilo **Scripts** · Vinicius **Combo de Monitoramento** ·
> Igor **MTCNA** · Daniel **V7** · Mauricio **Comunidade** · Alexandre **Scripts**

E a primeira mensagem de cada lado confirma a procedência:

| primeira mensagem | lado 12 díg | lado 13 díg |
|---|---|---|
| `user` (cliente escreveu) | **173** | 120 |
| `private_note` (ação interna) | 11 | **63** |
| `assistant` (envio ativo) | 19 | 14 |
| `conversation_event` | 0 | 6 |

O lado de 12 dígitos nasce do **cliente**; o de 13, de **ação interna**. Nos contatos
criados depois do cutover o vetor aparece explícito — envios de prospecção assinados por
**Gabriel Vargas** e **Anna Júlia**:

```
"Olá, tudo bem? Sou o Gabriel da equipe Redes Brasil..."
"Olá, Fabiana! Liberamos uma condição especial em um combo..."
```

## 4. Origem: veio do Chatwoot, não do WhatsBot

| Evidência | Valor |
|---|---|
| Pares 12×13 existentes no banco `chatwoot` | **466** |
| Pares importados para o WhatsBot | 203 |
| Pares cujo 2º contato nasceu **depois** do cutover (2026-07-20) | **0** |
| Última mensagem do lado 13 dígitos (global) | **2026-07-17** — véspera do cutover |
| Última mensagem do lado 12 dígitos (global) | 2026-08-04 — hoje |

O lado de 13 dígitos **morreu junto com o Chatwoot**. Desde o cutover, todo o tráfego
desses números cai no contato de 12 dígitos.

## 5. Por que o WhatsBot não gera pares novos

O core **já reconcilia as duas variantes** via `contact_inboxes`. Exemplo real —
contato 14857, criado pós-cutover:

| contact_id | phone | source_id |
|---|---|---|
| 14857 | `5569993802220` | `5569993802220` (13) |
| 14857 | `5569993802220` | `556993802220` (12) |

Duas linhas de `contact_inboxes`, um contato só: o inbound casa pela variante de 12 e não
cria contato novo. **33 contatos** já estão assim — mas **apenas 1 dos 406 duplicados**,
porque eles chegaram prontos do Chatwoot, sem a segunda linha.

É essa mecânica que o merge reproduz.

## 6. Escopo do merge

| tabela | linhas dos 406 contatos |
|---|---|
| `messages` | 34.270 |
| `plugin_protocolos_atendimentos` | 1.233 |
| `plugin_protocolos_protocolos` | 842 |
| `atendimentos` | 425 |
| `contact_inboxes` | 412 |
| `plugin_protocolos_avaliacoes` | 184 |
| `usage` | 74 |
| `executions` | 20 |
| `plugin_vendas_ia_conversa` | 18 |
| `plugin_agendamento_retorno_items` | 16 |
| `plugin_protocolos_ai_holds` | 13 |
| `contact_tags` | 12 |
| `observations`, `mentions`, `unread_msg_ids`, `janela_72h`, `melhorias`, `label_links` | 0 |

### Telefone denormalizado (não é FK — precisa de `UPDATE` explícito)

| coluna | linhas do lado perdedor |
|---|---|
| `plugin_protocolos_protocolos.contact_phone` | **490** |
| `plugin_protocolos_avaliacoes.contact_phone` | **72** |
| `plugin_agendamento_retorno_items.phone` | 4 |
| `plugin_debug_bus_records.phone` | 3 (descartável) |
| `executions.phone`, `plugin_janela_72h_windows.phone`/`source_id`, `plugin_melhorias_suggestions.contact_phone`, `plugin_vendas_ia_ad_leads.phone`/`source_id` | 0 |

`vw_atendimentos_whatsbot.telefone_contato` é **view** — deriva sozinha.

## 7. Constraints que o merge precisa respeitar

| objeto | efeito |
|---|---|
| `messages.contact_id` → `ON DELETE CASCADE` | **apaga 34.270 mensagens** se o `DELETE` vier antes do remapeamento |
| `atendimentos.contact_id` → `ON DELETE CASCADE` | idem para as 425 conversas |
| `plugin_protocolos_*`, `plugin_agendamento_retorno_items` | **sem FK** — viram órfãos silenciosos |
| `contacts_phone_key` UNIQUE(`phone`) | o perdedor precisa sair; não dá para "renomear e manter" |
| `contact_tags` PK(`contact_id`,`tag_id`) | colidiria se ambos tivessem a mesma tag — **0 colisões hoje** |
| `uq_contact_inbox_inbox_source` (`inbox_id`,`source_id`) | source_ids são distintos (12 vs 13) — **0 colisões hoje** |

### Índices únicos PARCIAIS — os bloqueadores

Dois índices impõem regras de negócio que o merge pode violar. Como tudo roda em transação
única, **um par bloqueado derruba os 203**:

| índice | regra | pares afetados |
|---|---|---|
| `uq_atend_open_contact_inbox` UNIQUE(`contact_id`,`inbox_id`) WHERE `status='open'` | um contato só pode ter **uma conversa aberta por inbox** | **1** |
| `plugin_protocolos_one_open_per_contact` UNIQUE(`contact_id`) WHERE `status='aberto'` | um contato só pode ter **um protocolo aberto** | 0 |

O par afetado é justamente o do Adilson: conversas 7213 e 12878, **ambas abertas no inbox 21**.
Mover a 12878 para o contato 9468 viola o índice. Por isso o fechamento de uma delas tem de
acontecer **antes** do merge, não depois (Passo 3.1 do plano).

Dois contatos perdedores têm protocolo aberto, mas os vencedores correspondentes não têm —
esses passam sem colidir.

## 8. Classificação dos 203 pares

| classe | pares | tratamento |
|---|---|---|
| A. nome idêntico | 62 | merge direto |
| B. nome similar (`similarity ≥ 0.35`) | 66 | merge direto — inclui o par do Adilson |
| C. um lado sem nome / só dígitos | 9 | merge direto |
| D. nome divergente | 66 | merge direto, exceto 5 — ver [03-TRIAGEM.md](03-TRIAGEM.md) |

A classe D **não** é sinônimo de suspeita: nome divergente é o esperado (pushName do
WhatsApp × nome do CRM). Aplicando os quatro sinais de troca de titular
(nome sem relação + períodos disjuntos + conversa real dos dois lados + intervalo > 6 meses),
sobram **5 pares** — e os cinco são explicáveis como a mesma pessoa. Detalhamento e a
refutação dos critérios fracos estão em [03-TRIAGEM.md](03-TRIAGEM.md).

**6 pares** têm conversas em **inboxes diferentes** — válido no modelo (um contato pode ter
conversas em vários inboxes), mas merecem conferência.

**1 par** tem as duas conversas abertas: exatamente o caso reportado (7213 + 12878).

## 9. Risco latente

| situação | contatos |
|---|---|
| 13 dígitos em DDD ≥ 31, **sem par ainda** | **212** (50 criados após o cutover) |
| 12 dígitos em DDD ≤ 28, sem par ainda | 42 |

Não são duplicatas hoje — o core reconcilia. Mas continuam sendo criados no formato
errado pela prospecção. Tratado em [04-PREVENCAO.md](04-PREVENCAO.md).

## 10. Órfãos que já existem (anteriores a este merge)

Como as tabelas do plugin `protocolos` não têm FK, o banco **já carrega órfãos** — linhas
apontando para `contact_id` que não existe mais, de limpezas anteriores:

| tabela | órfãos em 2026-08-04 |
|---|---|
| `plugin_protocolos_atendimentos` | 45 |
| `plugin_protocolos_protocolos` | 36 |
| `plugin_protocolos_avaliacoes` | 15 |
| `plugin_agendamento_retorno_items` | 3 |
| `plugin_protocolos_protocolos.contact_phone` | 1 |

Isso **não é causado pelo merge** e não precisa ser corrigido por ele. Mas muda o critério
de aceite: a verificação pós-merge compara contra **este baseline**, não contra zero. Se o
número subir, aí sim o merge deixou órfão.

## 11. Achado colateral

Nos 50 contatos criados pós-cutover pela prospecção: **23 falhas por janela de 24h**
(código 131047) e **9 por número inexistente no WhatsApp** (131026). É problema
operacional da prospecção, **fora do escopo deste plano**, mas vale reportar a quem cuida
das campanhas.
