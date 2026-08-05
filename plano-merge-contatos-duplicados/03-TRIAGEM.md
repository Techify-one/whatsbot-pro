# Triagem — quais pares mesclar

O objetivo é um só: **não juntar duas pessoas diferentes**. Um número de celular pode
trocar de titular, e nesse caso os dois contatos são legitimamente distintos ainda que o
número canônico coincida.

## Por que nome divergente NÃO é o critério

A classe D (66 pares) foi montada por similaridade textual, e a similaridade textual erra
aqui — porque **os dois lados têm fontes de nome diferentes por construção**:

| lado | fonte do nome | exemplo |
|---|---|---|
| 12 dígitos | pushName do WhatsApp (o cliente escolhe) | `Segcom`, `INFOSEG`, `~Yara Vila Nova`, `.`, `🤠` |
| 13 dígitos | cadastro do CRM / lista da campanha | `João Combo de Monitoramento`, `AUDROVANDO MEIRA DE LIMA` |

`Segcom` × `João Combo de Monitoramento` é quase certamente a **mesma** pessoa: a empresa
no WhatsApp, o titular na lista. Vale igual para apelido × nome completo (`Paulão` ×
`PAULO EDUARDO`, `Pdr` × `PEDRO HENRIQUE BERTULEZA`).

## Por que "períodos disjuntos" também não é critério

Medido em 2026-08-04, o padrão "períodos disjuntos + os dois lados com mensagem do
cliente" aparece em:

| classe | pares | com esse padrão |
|---|---|---|
| A — nome idêntico | 62 | **39** |
| B — nome similar | 66 | **40** |
| C — um lado sem nome | 9 | 8 |
| D — nome divergente | 66 | 39 |

Ele ocorre em **63% dos pares de nome idêntico** — ou seja, é o comportamento **normal**:
o cliente falava pelo contato antigo, a campanha criou o novo, e a conversa migrou. Usar
isso como sinal de alerta reprovaria dois terços dos pares comprovadamente legítimos.

**Todos os 66 pares da classe D têm mensagem do cliente nos dois lados.** Esse teste, sozinho,
não filtra nada.

## O critério que sobra: gap temporal longo + nomes sem relação

Aplicando os quatro sinais juntos — nome sem relação, períodos disjuntos, **conversa real
dos dois lados** (> 2 mensagens do cliente em cada) e **intervalo > 6 meses** entre o fim
de um lado e o começo do outro — sobram **5 pares** de 203. Nenhum com intervalo > 1 ano:

| canônico | id12 / nome | inbound | id13 / nome | inbound | períodos | gap |
|---|---|---|---|---|---|---|
| `557193180891` | 9819 · Paulo Adson | 6 | 9915 · PAULO ADOSN DA COSTA SOUSA | 8 | 24-10 / 25-06→26-06 | 237d |
| `554899084537` | 6966 · Gabriel | 6 | 7000 · Gabriel Combo de Segurança | 4 | 25-08→25-09 / 26-05→26-06 | 232d |
| `556696454456` | 9052 · INFOSEG | 119 | 9116 · Osnildo Recuperação | 25 | 25-03→25-08 / 26-02→26-03 | 207d |
| `558589906545` | 12031 · Yan | 37 | 12219 · YAN FEITOSA | 3 | 25-02→25-07 / 26-02 | 202d |
| `556799860093` | 9350 · Kennedy | 4 | 9356 · RONNY KENNEDY SILVA BALTA | 18 | 25-03 / 25-09 | 181d |

Olhando os nomes, **os cinco são explicáveis como a mesma pessoa**: quatro são apelido ×
nome completo (`Paulo Adson`, `Gabriel`, `Yan`, `Kennedy`) e um é empresa × titular
(`INFOSEG` × `Osnildo`).

**Conclusão da análise: nenhum par aparenta troca de titular.** A recomendação é mesclar os
203, conferindo estes 5 antes.

## O que fazer

1. Abrir no painel os **5 pares** da tabela acima. Ler as últimas mensagens do lado 12 e as
   primeiras do lado 13. Se a pessoa se identifica com o nome do outro lado, ou o
   atendimento tem continuidade temática → aprovar.
   Sinal de titular diferente seria uma apresentação do zero, sem qualquer referência ao
   histórico anterior.
2. Os demais 198 pares entram sem revisão individual.
3. Conferir por amostragem 3 pares das classes A, B e D depois do merge (Passo 8 do
   [02-PLANO-merge.md](02-PLANO-merge.md)).

O `sql/00-deteccao.sql` marca a coluna `alerta = REVISAR`, mas com o critério amplo
(sem o gap de 6 meses) — ele acusa 47 pares. Para chegar aos 5, filtrar também por
`gap > 180 dias` e `inbound > 2` nos dois lados, como na consulta registrada acima.

## Se um par for reprovado

O dano de um falso positivo é **limitado e reversível**: como as conversas **não são
fundidas**, mesclar dois titulares distintos coloca os históricos sob um contato só, mas
cada conversa continua separada e identificável. Desfazer é trabalhoso, não impossível.

Par reprovado simplesmente **não é tocado** — continua duplicado e volta a aparecer na
próxima detecção. Isso é intencional: reprovado não vira pendência esquecida.

## Registro da decisão

```
Data da triagem: ____________
Revisados manualmente: ____ pares (esperado: 5)
Reprovados (não mesclar): ids = [ ................................ ]
Aprovados: ____ pares
Responsável: ____________
```

A lista de reprovados vai para a variável `:reprovados` do `sql/02-merge.sql` — informar
**qualquer um dos dois ids** do par já exclui o par inteiro:

```bash
psql "$DSN" -v reprovados="ARRAY[9052,12219]::int[]" -f sql/02-merge.sql
```
