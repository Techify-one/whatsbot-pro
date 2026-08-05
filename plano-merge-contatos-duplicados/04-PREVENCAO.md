# Prevenção — fechar a torneira

O merge limpa o passado. Este documento trata do que continua acontecendo.

## O que já está resolvido pelo core

O WhatsBot **não gera pares novos**: quando um contato é criado com a variante "errada", o
inbound seguinte casa pela outra variante e ganha uma **segunda linha em `contact_inboxes`**
apontando para o mesmo contato. Evidência em produção — 33 contatos já estão assim, e
**zero pares** nasceram desde o cutover de 2026-07-20.

Ou seja: **não há bug de core a corrigir aqui**. O que sobra é higiene de dados.

## O que continua errado

| situação | contatos | por quê importa |
|---|---|---|
| 13 dígitos em DDD ≥ 31 sem par | **212** (50 pós-cutover) | telefone gravado num formato que o WhatsApp não usa nesses DDDs |
| 12 dígitos em DDD ≤ 28 sem par | 42 | o espelho do mesmo problema |

Não viram duplicata, mas ficam com o telefone canônico errado no cadastro — o que atrapalha
busca, exportação, integração com CRM e qualquer cruzamento por telefone (inclusive a ponte
do Trackify, que já casa mal por telefone).

## Causa: a prospecção usa o formato ANATEL

Os 50 contatos criados após o cutover vieram de envios ativos assinados por **Gabriel
Vargas** e **Anna Júlia**, a partir de listas onde o celular está no formato de 9 dígitos.
A pessoa digita `5569 9 9257-7740`; o WhatsApp, naquele DDD, trabalha com `5569 9257-7740`.

## Opções (nenhuma implementada — decidir antes)

### A. Normalizar na entrada do painel (recomendada)

Aplicar a forma canônica BR ao número **no momento de iniciar conversa nova**, antes de
criar o contato. O core já tem a noção de canônico BR no contrato de identidade de canal
(plano 32 F1, `channels/dedup.py`), então a regra existe — falta aplicá-la nesse caminho.

- **Prós**: elimina a causa; o operador não precisa saber da regra.
- **Contras**: mexe no core; exige cuidado para não normalizar número de outro país.
- **Escopo**: pequeno, mas é mudança de código — merece plano próprio em `docs-planos/`.

### B. Normalizar a lista antes da campanha

Passar as listas por uma normalização (remover o 9 para DDD ≥ 31) antes de importar.

- **Prós**: zero código no produto.
- **Contras**: depende de disciplina humana a cada campanha; falha em silêncio.

### C. Só detectar e limpar periodicamente

Rodar `sql/00-deteccao.sql` de tempos em tempos e mesclar o que aparecer.

- **Prós**: nada a construir — o script deste plano já serve.
- **Contras**: trata sintoma; o cadastro segue nascendo torto.

**Sugestão**: **A** como correção definitiva e **C** como rotina até A existir. B é frágil
demais para ser a única defesa.

## Limpeza opcional dos 212 latentes

Diferente do merge, aqui **não há par** — é um contato só, com o telefone no formato errado.
Duas saídas:

1. **Não fazer nada.** O core reconcilia quando o cliente responder. Custo zero, risco zero.
2. **Adicionar a variante correta** em `contact_inboxes` preventivamente, para que o primeiro
   inbound já case sem depender do caminho de reconciliação.

A opção 1 é suficiente e é a recomendada. A opção 2 só compensa se a limpeza de cadastro
(telefone canônico correto) virar requisito de alguma integração.

⚠️ **Não** trocar o `contacts.phone` dos 212 para a forma de 12 dígitos sem antes checar
colisão com contato existente — `contacts_phone_key` é UNIQUE e a troca pode falhar ou,
pior, criar exatamente o par que este plano está removendo.

## Fora de escopo (mas anotado)

Nos mesmos 50 contatos de prospecção pós-cutover: **23 falhas por janela de 24h**
(código 131047) e **9 por número sem WhatsApp** (131026). É qualidade de campanha, não
duplicação — vale levar a quem opera a prospecção.
