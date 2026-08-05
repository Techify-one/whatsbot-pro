# Plano de execução — merge dos pares

Alvo: `whatsbot`@10.8.100.5 (produção). **Nada aqui foi executado.**

## Regra do vencedor

O contato que **sobrevive** é o que carrega a forma que o WhatsApp entrega:

```
DDD 11..28  → vencedor = 13 dígitos (com o 9)
DDD 31..99  → vencedor = 12 dígitos (sem o 9)
```

Nos 203 pares atuais **todos são DDD ≥ 31**, então o vencedor é sempre o de 12 dígitos —
que é também o único lado com tráfego depois do cutover. A regra fica escrita em função do
DDD (e não fixada em "12 dígitos") para o script continuar correto se um par de DDD ≤ 28
aparecer depois.

## O que acontece com cada par

| item | destino |
|---|---|
| Mensagens, conversas, tags, observações, usage, menções | passam para o vencedor |
| Protocolos, ciclos, avaliações, agendamentos, vendas_ia | passam para o vencedor |
| `contact_inboxes` do perdedor | **movido** para o vencedor (é o que imuniza o número) |
| `contact_phone` / `phone` denormalizados | reescritos com o telefone do vencedor |
| Campos vazios do vencedor (`name`, `email`, `profession`, `company`, `address`) | preenchidos com o valor do perdedor |
| `unread_count` | somado |
| `is_pinned`, `is_archived`, `ai_enabled`, `contact_type` | mantidos **do vencedor** |
| Linha do perdedor em `contacts` | apagada **por último**, só se não sobrar dependente |

As **conversas continuam separadas**. O contato passa a ter o histórico completo, e cada
conversa preserva seu `display_id`, seus protocolos e sua timeline.

## Passo 0 — Backup fresco (obrigatório, no dia)

```bash
pg_dump -h 10.8.100.5 -U <user> -d whatsbot -Fc \
  -f ~/whatsbot-backups/whatsbot-PRE-MERGE-DUP-$(date +%Y%m%d-%H%M%S).dump
```

Confirmar tamanho > 0 e anotar o caminho aqui antes de seguir. Sem backup, não executar.

## Passo 1 — Detectar e exportar (READ-ONLY)

```bash
psql "$DSN" -f sql/00-deteccao.sql
```

Exportar a lista para revisão:

```bash
psql "$DSN" -At -F';' -f sql/00-deteccao.sql > pares-$(date +%Y%m%d).csv
```

Esperado hoje: **203** linhas, classes A=62 / B=66 / C=9 / D=66.

## Passo 2 — Triagem

Seguir [03-TRIAGEM.md](03-TRIAGEM.md). O resultado é uma lista de `id13` **reprovados**,
que vai na variável `:reprovados` do script de merge. Se nada for reprovado, usar
`ARRAY[]::int[]`.

## Passo 3 — Preflight (READ-ONLY)

```bash
psql "$DSN" -f sql/01-preflight.sql
```

Todas as verificações `*_deve_ser_zero` têm de voltar **0**. Qualquer valor diferente
**interrompe** o processo.

### Os dois bloqueadores reais

Existem dois **índices únicos parciais** que fazem o merge falhar — e como tudo roda em uma
transação, um par bloqueado derruba os 203:

| índice | regra | pares afetados hoje |
|---|---|---|
| `uq_atend_open_contact_inbox` | UNIQUE (`contact_id`, `inbox_id`) WHERE `status='open'` — um contato só pode ter **uma conversa aberta por inbox** | **1** (o par do Adilson) |
| `plugin_protocolos_one_open_per_contact` | UNIQUE (`contact_id`) WHERE `status='aberto'` — um contato só pode ter **um protocolo aberto** | 0 |

O bloco `1b` do preflight lista exatamente quais conversas precisam ser resolvidas.

## Passo 3.1 — Destravar as conversas em conflito (ANTES do merge)

Para cada linha do bloco `1b`, **resolver pelo painel** a conversa do lado perdedor
(coluna `conversa_do_perdedor_RESOLVER`).

Hoje é só uma: a **12878** do Adilson, parada desde 15/07, contra a 7213 que segue ativa.

Fazer **pela interface**, não por SQL — assim o `conversation_event` é gravado e os hooks do
plugin `protocolos` rodam normalmente. Depois de resolver, rodar o preflight de novo e
confirmar que `BLOQUEIA_conversa_aberta_deve_ser_zero` voltou a 0.

Se um protocolo aberto aparecer em conflito no futuro, o tratamento é o mesmo: fechar o do
lado perdedor pelo painel antes de mesclar.

## Passo 4 — Ensaio

O `sql/02-merge.sql` **termina em `ROLLBACK` por padrão**. Rodar assim primeiro:

```bash
psql "$DSN" -v reprovados="ARRAY[]::int[]" -f sql/02-merge.sql
```

Conferir na saída:

- `pares_processados` = 203 menos os reprovados
- `contatos_removidos` = igual a `pares_processados`
- `dependentes_remanescentes` = **0**
- nenhuma `EXCEPTION` no log

## Passo 5 — Commit

Com o ensaio limpo **e aprovação do usuário na hora**, trocar a última linha do script de
`ROLLBACK;` para `COMMIT;` e rodar de novo. O script é idempotente: um par já mesclado
some da detecção e não é reprocessado.

> Pelo MCP do vault a escrita exige `readOnly: false` e aprovação humana via Telegram.
> Por `psql`, a proteção é o `ROLLBACK` padrão — não editar o arquivo por engano.

## Passo 6 — Verificação

```bash
psql "$DSN" -f sql/03-verificacao.sql
```

Critério de aceite:

- **0 pares remanescentes** (fora os reprovados na triagem);
- órfãos **iguais ao baseline** do preflight — não a zero. O banco já tem órfãos
  anteriores (45 ciclos, 36 protocolos, 15 avaliações, 3 agendamentos); o que não pode
  é **aumentar**;
- contagem de mensagens, conversas, `contact_inboxes`, protocolos, ciclos e avaliações
  **idêntica** à de antes — o merge troca o dono das linhas, nunca as apaga;
- `contacts` diminui exatamente o número de pares mesclados;
- contatos com as duas variantes de `source_id` sobem de 33 para 33 + pares mesclados.

## Passo 7 — Smoke test no painel

1. Abrir o contato do Adilson: deve haver **um** contato com o histórico completo.
2. Enviar um template: deve aparecer **uma** conversa candidata, não duas.
3. Abrir 3 pares aleatórios das classes A, B e D: conferir ordem cronológica e protocolos.
4. Conferir a tela de protocolos: os 842 protocolos continuam vinculados.

## Rollback

Se a verificação falhar **depois do commit**, restaurar o dump do Passo 0:

```bash
pg_restore -h 10.8.100.5 -U <user> -d whatsbot --clean --if-exists \
  ~/whatsbot-backups/whatsbot-PRE-MERGE-DUP-<timestamp>.dump
```

Restauração é destrutiva para o que entrou **depois** do backup. Por isso a execução deve
acontecer em **janela de baixo tráfego**, e o backup ser tirado imediatamente antes.

Não há rollback parcial por par: o merge roda em transação única, então ou passa inteiro
ou não passa nada.

## Riscos e mitigação

| risco | mitigação |
|---|---|
| `DELETE` cascatear e apagar 34 mil mensagens | o script só apaga após checar **todas** as tabelas dependentes; se sobrar 1 linha, levanta `EXCEPTION` e aborta a transação |
| Órfãos nas tabelas do plugin `protocolos` (sem FK) | remapeadas explicitamente + verificadas no Passo 6 |
| **Índice único parcial derrubar os 203 por causa de 1 par** | Passo 3.1 destrava antes; o preflight recusa seguir enquanto `BLOQUEIA_*` não for 0 |
| Mesclar pessoas diferentes (número que trocou de dono) | triagem dos 5 candidatos ([03-TRIAGEM.md](03-TRIAGEM.md)); dano limitado porque as conversas não são fundidas |
| Mensagem nova chegando durante o merge | janela de baixo tráfego; o merge leva segundos, e uma mensagem que chegue no lado perdedor depois do merge cai no vencedor pelo `contact_inbox` movido |
| Alguém rodar o script sem querer | `ROLLBACK` é o padrão do arquivo |
