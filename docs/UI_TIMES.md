# Times na interface — contrato de apresentação

Este documento congela as decisões U0/U1 do plano 166. A autorização continua
no backend; a interface apenas representa capabilities devolvidas pelas APIs.

## Decisões de interface

- Sidebar: pilha compacta à direita. O responsável ocupa a primeira linha e o
  time fica logo abaixo; quando só um existe, não se reserva uma linha vazia.
- Operação em lote: o fluxo futuro usa preflight e resultado tudo-ou-nada. Não
  há chamada de bulk no frontend até os endpoints da fase R3 serem publicados.
- Administração: Times é a 3ª sub-aba da tela Usuários, ao lado de Grupos de
  permissão (`/users/teams[/id]`). Cada sub-aba tem a SUA permissão
  ([usersTabs.js](../web/static/js/services/usersTabs.js)): Usuários e Grupos pedem
  `users.manage`, Times pede `team.manage`; a tela abre para quem tem qualquer
  uma e quem só gerencia times vê só a aba Times. O antigo `/teams[/id]` segue
  como alias e é normalizado para `/users/teams` só depois do gate.
- Acesso: os rótulos são `Aberto`, `Oculto da lista` e `Privado`. “Restrito” não
  é usado porque não informa se o link direto também está protegido.
- Atribuição automática do time: um interruptor. Desligado = `manual` (o
  padrão: a conversa fica no time, sem responsável). Ligado, escolhe-se a
  estratégia — `Round-robin` (padrão ao ligar), `Atendente fixo` ou `Agente de
  IA`. ⚠️ O interruptor é só a leitura de `routing_mode`, sem coluna nova.
- Round-robin é **entre o time, não entre inboxes**: há UM cursor por time
  (`team_routing_state` com PK `team_id`), compartilhado por todas as caixas. A
  inbox da conversa continua só **filtrando a elegibilidade** — membro do time
  que não pertence à caixa é pulado, para não atribuir uma conversa que ele não
  consegue abrir.

## Catálogo normalizado

O adapter `services/teamCapabilities.js` aceita temporariamente o payload legado
e o payload com capabilities. Componentes recebem sempre o shape normalizado:

```text
team: {
  id, name, is_active, access_mode,
  readable, assignable, routable,
  unavailable_reason?
}

capabilities: {
  can_manage_teams,
  can_assign_own_team,
  can_assign_any_team,
  can_route_team,
  provided
}
```

Ausência de `routable` sempre resulta em `false`; `assignable` nunca é promovido
a routing. Em catálogo legado — identificado pela ausência total dos três flags
por time — `readable` e `assignable` mantêm o comportamento anterior. Em payload
novo parcial, capability ausente falha fechada.

O frontend não compara `member_user_ids`, não deriva permissão de `access_mode`
e não usa `conversation.assign` para autorizar mudança de time.

## Opções e referência atual

- `readableTeamOptions`: referências que podem ser apresentadas para leitura.
- `assignableTeamOptions`: destinos com `assignable === true`.
- `routableTeamOptions`: destinos com `routable === true`.
- O time atual fora do catálogo é mantido como referência somente leitura. Usa o
  nome projetado pela conversa; sem nome, mostra `Time indisponível`, nunca `#id`.
- Um destino desabilitado exibe `unavailable_reason` junto da opção e associa o
  texto por `aria-describedby`; não comunica indisponibilidade só por cor.

## Estados da UI

| Estado | Texto/ação | Origem de verdade |
|---|---|---|
| Carregando catálogo | `Carregando times…`; mantém referência atual | request em curso |
| Catálogo vazio | `Nenhum time disponível` | lista normalizada vazia |
| Sem permissão de agir | campo somente leitura; sem botão de ação | capabilities do servidor |
| Time inativo | nome com indicação `inativo`; nunca vira destino | `is_active` |
| Destino inelegível | motivo visível junto do item | `unavailable_reason` |
| Time atual desconhecido | `Time indisponível`, somente leitura | projeção da conversa + fallback |
| 403 | mensagem do servidor; estado local não muda | envelope `{error,status}` |
| 404 | informar indisponibilidade e ressincronizar | envelope `{error,status}` |
| 409 | explicar conflito preservando contexto | envelope `{error,status}` |
| Sucesso | aplicar conversa retornada/evento e fechar ação | resposta/WS do servidor |

Erros não são reescritos pelo adapter: `error` e `status` chegam intactos ao
consumidor. A mensagem fica próxima da ação e resultados importantes usam uma
região anunciada, não um aviso temporário que desaparece antes da leitura.

## Protótipos estáticos

```text
Sidebar larga                         Sidebar estreita
canal            👤 Responsável       canal          👤 Resp…
Contato          👥 Comercial         Contato        👥 Come…
última mensagem…          10:42       mensagem…        10:42
```

```text
Acesso                              Atribuição automática do time
(•) Aberto                          Atribuir conversas automaticamente  [ ○ ]  desligado = manual
( ) Oculto da lista                 ligado:  Estratégia [ Round-robin ▾ ]
( ) Privado                                    Round-robin · Atendente fixo [destino]
  [ ] Responsável pode visualizar              · Agente de IA [destino]
```

## Administração U2

Create e edit usam o mesmo `TeamForm` e enviam uma única intenção com dados,
membros, acesso e atribuição automática. A UI espera que o catálogo administrativo
`GET /api/teams?include_inactive=true`, gated por `team.manage`, entregue:

```text
{
  teams: [{ ..., conversation_count }],
  users: [{ id, name, email, is_active }],
  ai_agents: [{ agent_key, display_name, enabled }]
}
```

⚠️ **O layout copia o de Usuários e Grupos de permissão — não volte para duas
colunas.** O formulário é UMA coluna de campos de largura total (Nome, Descrição,
Membros, Acesso, Atribuição automática, IA), botões à direita no fim, sem rodapé
`sticky`; o cabeçalho é a descrição à esquerda + `+ Novo time` à direita e o card
da lista usa o mesmo esqueleto do `RoleCard` (`p-3`, edição abaixo de um
`border-t`). A versão anterior (`xl:grid-cols-2`, cada bloco numa caixa) ficava
visivelmente diferente das duas telas irmãs. Travado em `TeamsManager.test.js`.

Assim, alguém com `team.manage` não precisa receber `users.manage`. O hard delete
só aparece para time inativo quando `conversation_count === 0`; ausência da
contagem falha fechada. A tela não chama endpoints de routing/bulk.
