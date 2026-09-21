# Times na interface — contrato de apresentação

Este documento congela as decisões U0/U1 do plano 166. A autorização continua
no backend; a interface apenas representa capabilities devolvidas pelas APIs.

## Decisões de interface

- Sidebar: pilha compacta à direita. O responsável ocupa a primeira linha e o
  time fica logo abaixo; quando só um existe, não se reserva uma linha vazia.
- Operação em lote: o fluxo futuro usa preflight e resultado tudo-ou-nada. Não
  há chamada de bulk no frontend até o contrato R1/R3 ser publicado.
- Administração: a superfície futura é `/teams`, separada de Usuários e Grupos.
  O deep-link legado poderá redirecionar somente para quem tiver `team.manage`.
- Acesso: os rótulos são `Aberto`, `Oculto da lista` e `Privado`. “Restrito” não
  é usado porque não informa se o link direto também está protegido.
- Distribuição: `Manual`, `Round-robin`, `Atendente fixo` e `Agente de IA` são
  apenas o vocabulário visual aprovado. Campos e chamadas aguardam R1/R3.

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
Acesso                              Distribuição
(•) Aberto                          (•) Manual
( ) Oculto da lista                 ( ) Round-robin
( ) Privado                         ( ) Atendente fixo  [destino]
  [ ] Responsável pode visualizar   ( ) Agente de IA   [destino]
```

Os protótipos de administração não são código de produção. A implementação dos
campos depende do contrato administrativo/routing das fases U2 e R1/R3.
