# Merge de contatos duplicados por variante BR (12↔13 dígitos)

Consolidação dos contatos que existem **duas vezes** no WhatsBot de produção
(`whatsbot`@10.8.100.5) — o mesmo número gravado com e sem o **9º dígito**, herdado da
importação do Chatwoot. Foi o que fez um operador enviar um template e ver a conversa
duplicada (`/conversations/7213` e `/conversations/12878`, ambas do Adilson).

> **Status: PLANEJADO — nada foi executado.** A investigação rodou inteira em transação
> `READ ONLY`. Nenhuma linha de produção foi alterada.

Levantado em **2026-08-04**.

## Números

| Métrica | Valor |
|---|---|
| Pares duplicados | **203** (406 contatos) |
| Mensagens envolvidas | 34.270 |
| Conversas envolvidas | 425 |
| Protocolos / ciclos / avaliações | 842 / 1.233 / 184 |
| Pares nascidos **no WhatsBot** | **0** — todos vieram do Chatwoot |
| Pares com as duas conversas abertas | **1** (o caso reportado) |
| Contatos latentes (viram par se o cliente responder) | **212** |

## Causa em uma linha

O WhatsApp entrega **DDD 11–27 com 9 dígitos** (13 no total) e **DDD 31–99 sem o 9**
(12 no total). Os 203 pares são todos de **DDD ≥ 31** e ganharam uma segunda versão
"com 9" quando a prospecção ativa criou a conversa a partir de uma lista no formato
ANATEL. Detalhe completo em [01-DIAGNOSTICO.md](01-DIAGNOSTICO.md).

## Decisões que orientam o plano

1. **Vencedor = a forma que o WhatsApp entrega** (DDD ≥ 31 → 12 dígitos). Nos 203 casos
   isso resolve 203/203 para o lado de 12 dígitos, que é justamente o lado ainda ativo hoje.
2. **Conversas não são fundidas** — o contato perdedor cede suas conversas ao vencedor, e
   elas continuam separadas. Fundir destruiria a granularidade de protocolos e o histórico
   de atendimentos. O único par com duas conversas abertas é tratado à parte.
3. **O `contact_inbox` do perdedor é movido, não apagado.** É ele que imuniza o número:
   o contato passa a ter as duas variantes (12 e 13) apontando para si, exatamente como
   os 33 contatos que o WhatsBot atual já reconcilia sozinho.
4. **A triagem é por gap temporal, não por nome.** Nome divergente é o normal (pushName do
   WhatsApp × nome do CRM). Depois de refutar os critérios fracos, restam **5 pares** para
   conferência humana — e os cinco parecem ser a mesma pessoa. Ver [03-TRIAGEM.md](03-TRIAGEM.md).

## Baseline medido em 2026-08-04 (para comparar depois do merge)

| | |
|---|---|
| mensagens / conversas / contact_inboxes | 651.630 / 15.136 / 14.961 |
| protocolos / ciclos / avaliações | 15.526 / 21.318 / 2.380 |
| contatos | 14.896 |
| contatos já imunizados (duas variantes) | 33 |
| órfãos **pré-existentes** (protocolos / ciclos / avaliações / agendamentos) | 36 / 45 / 15 / 3 |

Só `contacts` pode diminuir (−203). Órfãos não podem **aumentar** — não precisam ser zero.

## Os dois perigos

**1. `ON DELETE CASCADE`.** `messages.contact_id` e `atendimentos.contact_id` cascateiam.
Um `DELETE FROM contacts` no perdedor **antes** do remapeamento apaga as mensagens e as
conversas dele em silêncio, sem erro. As tabelas do plugin `protocolos` são o oposto —
**não têm FK nenhuma**, então ficam órfãs apontando para um contato inexistente. O script
trata os dois casos e **recusa o `DELETE` enquanto houver qualquer dependente**.

**2. Índice único parcial.** `uq_atend_open_contact_inbox` proíbe duas conversas abertas do
mesmo contato no mesmo inbox — e o par do Adilson tem exatamente isso. Como o merge roda em
transação única, **esse par sozinho derrubaria os 203**. Por isso o Passo 3.1 resolve a
conversa duplicada **antes** de mesclar.

## Estrutura

```
plano-merge-contatos-duplicados/
├── README.md              # este índice
├── 01-DIAGNOSTICO.md      # investigação: causa raiz, origem, escopo
├── 02-PLANO-merge.md      # execução passo a passo (backup, ensaio, commit)
├── 03-TRIAGEM.md          # critério de aprovação par a par
├── 04-PREVENCAO.md        # os 212 latentes e o vetor da prospecção
└── sql/
    ├── 00-deteccao.sql    # READ-ONLY  — lista e classifica os pares
    ├── 01-preflight.sql   # READ-ONLY  — colisões e invariantes antes de escrever
    ├── 02-merge.sql       # ESCRITA    — roda em ENSAIO (ROLLBACK) por padrão
    └── 03-verificacao.sql # READ-ONLY  — prova que o merge fechou
```

## Ordem de execução

1. `00-deteccao.sql` → exportar a lista dos 203 pares
2. [03-TRIAGEM.md](03-TRIAGEM.md) → conferir os 5 candidatos; anotar reprovados
3. `pg_dump` de produção — **fresco, no dia**
4. `01-preflight.sql` → tudo `*_deve_ser_zero` em zero
5. **Destravar** a conversa duplicada do Adilson pelo painel → rodar o preflight de novo
6. `02-merge.sql` em **ensaio** (`ROLLBACK`) → conferir os contadores
7. `02-merge.sql` em **commit**, com aprovação na hora
8. `03-verificacao.sql` → órfãos no baseline, zero pares remanescentes
9. [04-PREVENCAO.md](04-PREVENCAO.md) → fechar a torneira

Nenhum passo depende de deploy de código: o merge é 100% dados.

Requisito: **psql ≥ 10** (o `02-merge.sql` usa `\if`).
