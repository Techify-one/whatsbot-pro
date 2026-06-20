> ⚠️ **ARQUIVADO (2026-06-19).** Documento de apoio à decisão cuja função já foi cumprida:
> todas as 74 perguntas funcionais que ele explicava estão **DECIDIDAS** em
> [`../DECISOES.md`](../DECISOES.md) (Lote 2 e Lote 3). Mantido só como histórico.
> **Não usar como referência de estado** — perguntas aqui descritas como "em aberto" já foram
> respondidas. Fonte da verdade das decisões: `DECISOES.md`.

# Explicações das perguntas que faltam responder

> As 15 perguntas que ainda dependem da sua decisão, explicadas em linguagem simples, cada uma com
> uma recomendação para você só dizer "ok" ou ajustar. Ver status em [`DECISOES.md`](DECISOES.md) e
> o texto original em [`00-plano-mestre.md` §5](00-plano-mestre.md).
> Responda no formato curto, ex.: `P4 ok`, `P29 ctypes`, `P57 1 worker`.

---

## Tema A — Conversas

### P1 — Ordem de construção / FKs stub
É só a ordem técnica: a tabela `conversations` precisa apontar para `inboxes` e `users`, que são
criados em outros planos. A recomendação é o plano 01 criar "stubs" (esqueletos mínimos) dessas
tabelas sem amarração rígida, e os planos 02/03 completarem depois. Decisão puramente interna, sem
impacto no produto.
**Recomendo:** opção (a) stubs.

### P4 — Status inicial da conversa quando a IA está ligada: `open` ou `pending`?
Quando chega uma mensagem nova e a IA vai responder sozinha, a conversa deveria já aparecer como
"aberta" na fila do atendente, ou ficar num estado "pendente" (= "o robô está cuidando, humano ainda
não precisa olhar")? No Chatwoot, quando há bot, a conversa nasce `pending` e só vira `open` quando
precisa de humano. É mais organizado, mas adiciona um estado. Como no P3 você quis só `open`/`closed`
por ora, o natural é nascer `open` e usar o indicador de "IA ativa" para mostrar que o robô está
atendendo.
**Recomendo:** nascer `open` (deixa `pending` para quando você adicionar o estado "aguardando").

### P5 — Cascata de IA (quem manda no "responder ou não": inbox, conversa ou contato?)
Hoje existe um único liga/desliga da IA por **contato** (`ai_enabled`). Com o modelo novo, faz
sentido poder ligar/desligar a IA em 3 níveis: na **inbox inteira** (ex.: "essa caixa é só humana"),
na **conversa específica** (ex.: "essa conversa eu assumo") e no **contato** (default). A regra seria
em cascata: a inbox manda; dentro dela, a conversa manda; e o contato é o padrão para conversas
novas. O ponto que precisa do seu OK: hoje o botão "desligar IA" mexe no contato; passaria a mexer
**na conversa atual**.
**Recomendo:** adotar a cascata inbox → conversa → contato; o botão de toggle passa a agir na
conversa.

---

## Tema B — Canais e runtime

### P13 — "Webhook por device no GOWA"
Não é sobre mandar webhook para fora. É sobre **como o WhatsBot sabe de qual número veio a mensagem**
quando houver vários números no mesmo GOWA. O GOWA manda tudo para um endpoint único; cada mensagem
traz um `device_id` no corpo. A pergunta é se confiamos nesse `device_id` para rotear a mensagem para
a caixa de entrada certa.
**Recomendo:** usar o `device_id` do payload + um caminho próprio por canal, confirmando nos testes
que o `device_id` vem em todos os tipos de evento.
*(Mandar webhook para sistemas externos é outra feature, não está nesta pergunta.)*

### P15 — "Por que chave mestra de cifragem?"
Porque a versão Pro vai guardar **segredos de terceiros no banco**: o token da API oficial do
WhatsApp (Cloud API), futuras credenciais de Telegram/e-mail, etc. Guardar esses tokens em texto puro
é arriscado (quem vir o banco vê tudo). Então criptografamos antes de salvar, e para isso precisamos
de uma "chave mestra". A pergunta é onde guardar essa chave: no Docker dá para usar `.env`; no EXE
Windows não tem `.env`.
**Recomendo:** usar variável de ambiente quando existir; senão, gerar um arquivo `storages/secret.key`
no primeiro boot (com aviso de que perder esse arquivo invalida os tokens salvos).

### P20 — "Descobrir o número real de um device GOWA"
Quando você conecta um número via QR, o GOWA nem sempre devolve o número de forma óbvia depois. Para
mostrar "Canal: +55 11 9..." na tela, precisamos capturar e guardar esse número.
**Recomendo:** capturar o número logo após o login e salvar na tabela de canais (aceitando ficar em
branco até o primeiro login).

### P21 — "Contrato de export de provider / lifecycle"
É só **o formato pelo qual um plugin de canal se declara** para o sistema. Hoje os plugins declaram
coisas no `plugin.yaml` (estilo declarativo). A pergunta é se o provider de canal segue o mesmo estilo
(declarar `entry.channels` no manifest) ou se também permitimos um registro "imperativo" (código que
chama `register()` na mão).
**Recomendo:** só o declarativo, consistente com o resto do sistema de plugins.

### P27 — "`stop_event` por-task vs global"
Detalhe de como o supervisor **desliga as tarefas de fundo** quando o servidor para. Hoje há um sinal
global de "pare tudo". A opção A usa o mecanismo nativo do Python (`task.cancel()`) — mais limpo e
moderno — mantendo o sinal global só por compatibilidade durante a transição.
**Recomendo:** opção A (`cancel()` nativo). Puramente interno, sem impacto no produto.

### P29 — "Die-with-parent no Windows + e no Linux?"
"Die-with-parent" = garantir que, se o WhatsBot morrer, os subprocessos (como o GOWA) **morram
junto**, sem virar processos órfãos consumindo recursos ou segurando a sessão do WhatsApp.
- No **Linux** (seu servidor Pro) isso é resolvido nativamente e bem (`PR_SET_PDEATHSIG`) — **sem
  problema nenhum**.
- No **Windows** (EXE) é mais chato; dá para fazer com `ctypes` (sem dependência extra) ou com a
  biblioteca `pywin32` (mais limpa, mas engorda o EXE).

Como seu foco Pro é Linux/servidor, a complexidade do Windows é secundária.
**Recomendo:** `ctypes` no Windows (sem dep nova) + uma rede de segurança que mata processos GOWA
"perdidos" no boot. No Linux você não precisa se preocupar.

---

## Tema D — Respostas rápidas

### P42 — "Índice único por escopo: parcial vs coluna gerada"
Puramente técnico: como o banco garante que não existam dois atalhos `/oi-anna` no mesmo escopo. São
duas maneiras de implementar a mesma regra (e conecta com sua decisão no P41 de bloquear nomes
duplicados).
**Recomendo:** índices parciais (opção a) — mais direto. Sem impacto visível.

### P44 — "Carregar a lista de atalhos: refetch vs cache" (prós/contras)
Quando o atendente digita `/`, o sistema precisa ter a lista de atalhos disponível. Duas formas:
- **(a) refetch** — buscar a lista do servidor toda vez que abre uma conversa. *Prós:* sempre
  atualizada, simples. *Contras:* uma requisição a cada conversa aberta (desperdício se a lista muda
  pouco).
- **(b) cache + evento** — carregar a lista uma vez, guardar no navegador, e só recarregar quando
  alguém editar um atalho (avisado por evento). *Prós:* rápido, quase sem requisições. *Contras:* um
  pouco mais de código; precisa do evento de invalidação para não ficar desatualizada.

Como a lista muda pouco e é usada o tempo todo, **recomendo (b) cache + evento** (foi o que você já
escolheu — aqui é só o porquê).

### P47 — "Variáveis `{{agent.*}}`/`{{inbox.*}}` antes dos planos 02/03"
Os atalhos podem ter variáveis como `{{contact.name}}`. Algumas variáveis (nome do atendente, nome da
inbox) dependem de features que ainda não existem nas primeiras fases. A pergunta é o que fazer com
essas variáveis enquanto a fonte não existe: deixar virar texto vazio, ou escondê-las do menu?
**Recomendo:** esconder do catálogo o que ainda não existe e, se aparecer, expandir como vazio — assim
o atendente não vê variáveis quebradas.

---

## Tema F — Motor de IA

### P57 — "Workers do uvicorn / o que tem a ver com o Coolify"
O uvicorn (servidor web) pode rodar com vários "workers" (processos paralelos) para aguentar mais
carga. O Coolify é onde você hospeda — ele pode estar configurado para subir N workers. O problema: o
hot-reload das tools/agentes guarda coisas **na memória de um processo**; com vários workers, um
worker atualiza e os outros ficam desatualizados.
**Recomendo:** rodar com 1 worker no início (a invalidação por evento + um cache curto resolvem). Se
um dia a carga exigir vários workers, aí adotamos um mecanismo de sincronização entre eles.
**Para você decidir:** hoje seu Coolify roda o WhatsBot com 1 worker ou vários? Se não souber, fica em
1 (default) e está resolvido.

### P65 — "Tempo de coexistência legacy × Agno"
O motor de IA atual (handler legado) e o novo (Agno) vão conviver durante a migração. A pergunta é por
quanto tempo manter os dois: pouco (1-2 semanas, até confirmar que o novo faz tudo igual, e remover o
velho) ou muito (meses, como rede de segurança). Manter os dois por muito tempo = código duplicado
para manter.
**Recomendo:** coexistência curta — rodar os dois em paralelo só até comprovar que o Agno faz tudo que
o atual faz, e então aposentar o legado.

---

## Tema H — Filtros

### P82 — "Encadeamento de revisões Alembic"
Alembic é a ferramenta de migrations do banco. Cada mudança de schema é uma "revisão" encadeada. Como
vários planos criam migrations, elas precisam ficar numa fila linear (uma após a outra), senão o
Alembic cria "galhos" (branches) que dão dor de cabeça.
**Recomendo:** encadeamento linear — cada migration nova aponta para a última existente no momento de
implementar. Disciplina de desenvolvimento, sem impacto no produto.

---

## Resumo para responder rápido (todas com a recomendação)

| P | Recomendação curta |
|---|--------------------|
| P1 | stubs (a) |
| P4 | nascer `open` |
| P5 | cascata inbox→conversa→contato; toggle age na conversa |
| P13 | usar `device_id` do payload + path por canal |
| P15 | env quando houver, senão `storages/secret.key` no 1º boot |
| P20 | capturar número após login e salvar |
| P21 | só declarativo (`entry.channels`) |
| P27 | `task.cancel()` nativo (a) |
| P29 | `ctypes` no Windows + stale-kill; Linux nativo |
| P42 | índices parciais (a) |
| P44 | cache + evento (b) |
| P47 | esconder o que não existe + expandir vazio |
| P57 | 1 worker (confirmar se o Coolify usa 1 ou vários) |
| P65 | coexistência curta (1-2 semanas) |
| P82 | linear |
