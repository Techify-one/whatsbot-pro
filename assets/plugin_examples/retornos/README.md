# Retorno Automático — plugin `retornos`

Follow-up automático por **configuração de regras**, porte do módulo Retornos do Nexus
(plano 76 — o arquivo saiu de `docs-planos/` depois de executado; está no histórico do git).

Substitui a função de follow-up automático do plugin antigo `retorno_automatico` (que é
**desativado** na ativação deste, para não duplicar nota) e convive com o
`agendamento_retorno` (o retorno **manual** agendado pelo atendente).

> **Vocabulário** — o que o plano 76 chama de *régua* e *passo* passou a se chamar
> **configuração** e **retorno** (tabelas, rotas e UI). A migration `003` renomeia o
> esquema existente, então nenhum dado se perde.

## O que é uma configuração

```
Configuração ──► Retorno 1 ──► regras ──► mensagens (texto / nota / mídia / IA)
                 Retorno 2 ──► regras ──► mensagens
                 …
```

* Quando o **cliente manda mensagem**, a primeira configuração **ativa** (de cima para baixo na
  lista) assume a conversa e agenda o retorno 1. **Não há filtro de entrada na configuração** — quem
  decide se o follow-up sai são as **regras de cada retorno**, avaliadas na hora do disparo
  com dados frescos.
* A cada minuto o verificador reavalia o retorno atual com dados **frescos**:
  * regras **passaram** → dispara as mensagens e agenda o retorno seguinte;
  * regras **não passaram** → **reagenda o MESMO retorno** (nunca pula fingindo progresso) até
    esgotar o **teto global de segurança** (settings do plugin: máximo de reavaliações e prazo
    máximo do retorno), e então marca `expirado`.
* O retorno **não tem espera própria** (migration `007`): *quando* disparar é decisão das
  condições (hora, dia, horas desde o último contato…), avaliadas com dados frescos. Retorno sem
  condição temporal é reavaliado já no ciclo seguinte.
* Uma nova mensagem do cliente **reinicia** ou **encerra** a configuração (escolha por configuração).
  Resolver o atendimento, um atendente assumir, ou desligar a IA cancelam conforme os
  toggles da configuração.

### Exportar / importar JSON

Uma configuração inteira (campos + retornos + regras + mensagens) viaja como JSON — o
mesmo formato nos dois sentidos, sem `id`/`created_at`/`updated_at`:

| Onde | Botão | O que faz |
|---|---|---|
| Lista de configurações | **Importar JSON** | `POST /configuracoes/import` — cria uma configuração **nova**, sempre **desativada** |
| Cabeçalho da configuração aberta | **Exportar JSON** | `GET /configuracoes/{id}/export` — baixa o arquivo |
| Cabeçalho da configuração aberta | **Importar JSON** | `POST /configuracoes/{id}/import` — **substitui ESTA configuração** pelo arquivo (pede confirmação) |

O import no cabeçalho é destrutivo por contrato (é o par do "Exportar JSON" dali): os
retornos e mensagens atuais são apagados e recriados a partir do arquivo, tudo numa
transação só — o verificador nunca enxerga a configuração no meio da troca. **`ativo` e
`posicao` não vêm do arquivo**: o liga/desliga e o lugar na lista são da instância que está
rodando, então importar não acende nem apaga uma configuração pelas costas. Agendamento em
andamento parado num retorno que deixou de existir volta ao retorno 1 na próxima avaliação.

## Tipos de mensagem de um retorno

| Tipo | O que faz |
|---|---|
| `text` | Mensagem normal ao cliente (pelo canal da conversa) |
| `private_note` | Nota privada — só o atendente vê (painel-only) |
| `ia_responde_agora` | A **instrução** do retorno vira um turno do agente (AGNO) e a resposta É enviada ao cliente. É o equivalente do `@Bia` do Nexus, mas sem bot externo |
| `image` · `audio` · `video` · `document` | Mídia (upload no editor ou URL pública) |

**O anexo tem de casar com o tipo** ([media_kinds.py](media_kinds.py) + o espelho
[static/mediaKinds.js](static/mediaKinds.js), com os mesmos vetores nos dois testes):
`image`/`audio`/`video` só aceitam arquivo daquela categoria — **`document` aceita qualquer
formato** (imagem, vídeo e PDF são documentos válidos). O seletor de arquivo já abre filtrado
(`accept`), mas o filtro do navegador é só sugestão, então a checagem real acontece em três
pontos: ao escolher o arquivo, no `POST /upload` e ao **salvar a mensagem** (que é onde cai
quem troca só o tipo depois de anexar). Trocar o tipo na tela remove o anexo incompatível na
hora, com aviso, e mensagens já gravadas em conflito (import, dado antigo) ganham o selo
vermelho *arquivo incompatível*. A regra é conservadora: só bloqueia quando reconhece que o
arquivo é de OUTRA categoria — arquivo sem extensão conhecida e MIME genérico passa (aí quem
julga é o provedor, pelas `MediaLimits` do canal). Sem isso, o erro só apareceria no disparo:
o `tipo` vira o `kind` do `send_media` e o provedor recusa o `.mp4` declarado como imagem.

**Conferir o anexo**: o nome do arquivo enviado (e o botão *Ver arquivo*, que também cobre o
caso da URL) abre um modal com a **imagem**, o **player de áudio/vídeo** ou o **leitor de
PDF** — [static/MediaPreview.js](static/MediaPreview.js). Vale já para o arquivo recém-enviado,
antes de salvar a mensagem. O modo de exibir sai da categoria REAL do arquivo (extensão), não
do tipo da mensagem: um `.docx` mandado como *Documento* não tem player e vira link de
download. Arquivo **em URL externa** não pode ser renderizado embutido (a CSP do painel é
`img-src/media-src 'self'`) — o modal mostra o aviso e o botão *Abrir em nova aba*; o mesmo
cartão aparece se o arquivo tiver sumido do disco (`statics/` não é persistente por padrão).

O **Teste A/B** é do **retorno inteiro** (checkbox ao lado de "Mensagens deste retorno"), não de
cada mensagem: ligado, cada disparo envia **uma só** das mensagens do retorno, alternando na
ordem (cursor `proxima_mensagem_index`) — desligado (default), todas saem, na ordem.

A **pausa entre as mensagens** (campo ao lado da mesma explicação, em **segundos**) é **por
retorno** (coluna `delay_mensagens_seg`): é o intervalo entre uma mensagem e a seguinte
**dentro do mesmo disparo**. Deixar em branco = herda a pausa **global** do plugin (setting
`delay_between_messages_seconds`, no modal *Configurar*) — todo retorno criado antes da 1.6.0
começa assim, sem mudança de comportamento. Salva sozinha ao sair do campo (não depende do
botão "Salvar retorno"). Teto de **300 s** por mensagem e de **300 s por disparo**: o ciclo do
verificador é serial, então uma pausa longa segura os outros agendamentos do mesmo minuto —
estourado o teto do disparo, as mensagens restantes saem sem pausa (com WARNING no log).
Durante a pausa o lock do controle é **renovado a cada 30 s**, senão o `recover_stale_locks`
(5 min) devolveria o agendamento à fila no meio do disparo e o cliente receberia a mensagem
duplicada. Só tem efeito com 2+ mensagens saindo no mesmo disparo (com A/B ligado sai uma só).

## Regras travadas na implementação

| # | Comportamento |
|---|---|
| D3 | Fora da **janela de 24 h** da Meta, o retorno vira uma **nota privada de aviso** (sem template HSM) |
| D5 | Regra falsa **reagenda o mesmo retorno** (até o teto global de reavaliações/prazo) — nunca avança |
| D6 | O contador de disparos sobe **só quando ao menos uma mensagem saiu** |
| D7 | `entre` em campo de **hora** atravessa a meia-noite (`16:00`–`07:30` num único operador) |
| D8 | "Quando o cliente responder" (reiniciar × encerrar) é de fato lido |
| D9 | Fuso é **offset fixo por configuração** (padrão −3) — nunca o TZ do processo |
| — | `ia_responde_agora` NÃO re-checa o interruptor global de IA, mas checa sempre os gates da conversa (IA desligada / humano assumiu / tag `transferido_atendente`) |
| — | `ia_responde_agora` grava uma **nota privada** com a instrução INTEIRA DEPOIS do disparo (o turno sintético não vai ao histórico; a nota é o único rastro de que a mensagem veio de um retorno). O painel recolhe nota privada longa num chip de uma linha, com seta pra expandir |
| — | Todo envio deste plugin registra a **supressão de eco** do core (`state.recently_sent` antes do envio + id externo depois): sem isso, canais que ecoam o próprio envio (Messenger/GOWA) gravavam a mesma mensagem 2× — a 2ª como bolha "Manual" (`status='operator'`) |

## Arquivos

| Arquivo | Papel |
|---|---|
| `rules.py` | Motor de regras **puro** (recursivo, operadores, hint de agendamento) |
| `static/rules.js` | Espelho 1:1 do motor em JS (preview da UI) + `rules.test.js` (`node --test`) |
| `catalog.py` | Catálogo de campos/operadores (vocabulário compartilhado backend ↔ UI) |
| `contrib.py` | Campos que **outros plugins** contribuem ao construtor (seams do bus) |
| `evalctx.py` | Monta o contexto de avaliação de uma conversa (repos do core, sem rede) |
| `repo.py` | Data-access das 5 tabelas + **lock atômico** do dispatcher |
| `dispatcher.py` | O ciclo por minuto (recovery → lock → grace → avaliar → disparar/reagendar) |
| `actions.py` | Executores por tipo de mensagem (gate de 24 h, gates da IA) |
| `media_kinds.py` | Categoria do anexo × tipo da mensagem (imagem/áudio/vídeo × documento) |
| `static/mediaKinds.js` | Espelho 1:1 do guarda de anexo + `mediaKinds.test.js` (`node --test`) |
| `static/MediaPreview.js` | Modal de pré-visualização do anexo (imagem/áudio/vídeo/PDF/download) |
| `events.py` | Bus: entrada/reset/cancelamento |
| `lifecycle.py` | `spawn_task('scheduler')` + aposenta o `retorno_automatico` |
| `routes.py` | REST em `/api/plugins/retornos/...` |
| `static/retornos.js` | A tela (abas **Configurações** e **Monitor**) |
| `static/RegraBuilder.js` | Construtor visual recursivo de regras (grupos E/OU) + arrastar e soltar |
| `static/tree.js` | Movimento de nós na árvore (caminho, remover/inserir, conector) + `tree.test.js` |

## Reorganizar arrastando (⠿)

Cada condição e cada grupo têm uma **alça** à esquerda. Arrastando por ela dá para reordenar no
mesmo nível, **tirar uma condição de um grupo**, **soltar dentro de outro grupo** e mover um
**grupo inteiro com o conteúdo**. As faixas de soltura só acendem onde o movimento é aceitável.

| Regra | Por quê |
|---|---|
| O conector **E/OU acompanha o item** | Ele é do item na árvore, não da posição — uma condição marcada `OU` continua `OU` no destino |
| Quem cai na 1ª posição **perde o conector** (vira `se`); quem sai da 1ª ganha `E` | É a invariante do motor: a 1ª regra da lista é o acumulador inicial |
| Grupo **não entra em si mesmo** nem num descendente | Criaria um ciclo |
| O aninhamento respeita o **teto de 5 níveis** | O mesmo do botão "+ Grupo" — um grupo alto não cabe numa lista funda |
| Soltar nas **bordas do próprio item** não faz nada | Não é movimento; a faixa nem acende |

⚠️ Reordenar **muda a lógica**: o motor avalia da esquerda para a direita, sem precedência de
`E` sobre `OU`. Quem depende de precedência usa **grupo** (os parênteses).

Arraste de **mouse** (API nativa do navegador). Em tela de toque nada muda — não havia
reordenação antes.

## Campos de outros plugins no construtor

O select de condições é extensível: qualquer plugin ATIVO pode oferecer os próprios campos,
que aparecem depois dos do core, sob a divisória **"Configurações (outros plugins)"**. Este
plugin não conhece nenhum outro por nome — ele publica dois filtros no bus e consome o que
voltar (`contrib.py`). Desativar o plugin contribuinte some os campos do select sozinho.

```python
# <seu_plugin>/filters.py
def campos(ctx, meta):
    meta["grupos"].append({"id": "vendas", "label": "Vendas", "plugin_id": "vendas"})
    meta["campos"].append({"id": "vendas.etapa", "label": "Etapa do funil",
                           "grupo": "vendas", "tipo": "enum",
                           "options": [{"value": "novo", "label": "Novo"}]})
    return meta

def contexto(ctx, valores):
    conv_id = ctx.extras.get("conversation_id")      # também: contact_id, phone, channel_id, now
    valores["vendas.etapa"] = etapa_da_conversa(conv_id)
    return valores

FILTERS = {"filter.retornos.campos": campos, "filter.retornos.contexto": contexto}
```

Regras: `tipo` ∈ `text`/`number`/`date`/`time`/`select`/`enum`/`multi-select` (desconhecido cai
em `text`); `options` é uma lista `{value,label}` ou a CHAVE de uma lista posta em
`meta["opcoes"]`; campo sem grupo declarado, ou que colida com um id do core, é descartado —
o core sempre ganha. Um valor devolvido para um campo não declarado é ignorado pelo motor
(campo desconhecido ⇒ condição falsa). Exemplo real: `protocolos/retornos_fields.py`.

## Filtros e paginação do Monitor

A barra acima da tabela recorta os agendamentos **no servidor** (`GET /monitor/controles`),
nunca sobre a página já carregada:

| Filtro | Parâmetro | Observação |
|---|---|---|
| Status | `status` | `active` (padrão), `todos`, `completed`, `cancelled`, `expired` |
| Configuração | `configuracao_id` | Select alimentado por `GET /monitor/filtros` |
| Disparos | `disparos` | Campo **digitável** (não é select): casa EXATAMENTE com a coluna "Disparos" (`disparos_enviados`). `0` é filtro legítimo — quem ainda não disparou —, então só o campo VAZIO não filtra |
| Próximo de / até | `next_from` / `next_to` | Epochs sobre `next_at`. Quem converte o DIA do calendário é o **navegador**, no fuso dele — o servidor só compara epoch. Agendamento **sem** `next_at` fica de fora quando qualquer um dos dois é passado |
| Por página | `limit` / `offset` | 25/50/100/200 (padrão 50); teto de 1000 no servidor |

A tabela **pagina no servidor**, igual à aba Eventos: a rota devolve
`{items, total, limit, offset}`, e o `total` vem de `repo.count_controles` com a MESMA
cláusula da página (`_controle_where`) — contar sobre a página diria "1 de 50" para sempre.
O `ORDER BY` termina em `c.id DESC` porque os critérios de cima empatam com facilidade
(vários `next_at` NULL, mesmo `updated_at`) e, sem desempate estável, a mesma linha
apareceria em duas páginas — ou em nenhuma. Trocar qualquer filtro volta à página 1, e uma
página que ficou além do fim (cancelou a última linha, o verificador concluiu agendamentos)
recua sozinha para a última página com conteúdo.

O select de configuração sai de `GET /monitor/filtros` (`{configuracoes}`), recarregado junto
com a tabela a cada `retornos_changed`. Se essa chamada falhar, ele degrada para "Todas" e a
tabela continua funcionando. Os cartões do topo continuam sendo o total **global** por status
(`/monitor/stats`) — eles não seguem o filtro nem a página.

## Tabelas

`plugin_retornos_configuracoes` · `plugin_retornos_retornos` · `plugin_retornos_mensagens` ·
`plugin_retornos_controle` (1 por conversa, `conversation_id` UNIQUE) · `plugin_retornos_log`

## Permissões (RBAC)

| Chave | Para quê |
|---|---|
| `plugin.retornos.view` | Ver a tela (configurações + monitor) — sem ela a entrada nem aparece no menu |
| `plugin.retornos.edit` | Criar/editar/excluir configurações, retornos e mensagens |
| `plugin.retornos.monitor` | Reiniciar/cancelar agendamentos em andamento |

## Testes

```bash
venv/bin/python -m pytest tests/test_retornos_rules.py tests/test_retornos_plugin.py \
                          tests/test_retornos_campos_de_plugins.py -q
node --test assets/plugin_examples/retornos/static/rules.test.js \
             assets/plugin_examples/retornos/static/mediaKinds.test.js \
             assets/plugin_examples/retornos/static/tree.test.js
```

Os dois motores (Python e JS) usam os **mesmos vetores** — se um caso divergir, o preview da
UI está mentindo.
