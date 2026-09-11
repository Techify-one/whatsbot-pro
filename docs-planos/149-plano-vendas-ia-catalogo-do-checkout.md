# Plano 149 — O `vendas_ia` passa a ler o catálogo do módulo Checkout: correções que já quebram hoje, migração do de-para de campanha e o preço estruturado

> **Status:** 🟢 **EXECUTADO** (2026-08-31) — F0–F9 + o re-seed versionado das tools + release `vendas_ia` **1.10.0** publicado localmente (ver §7) · **Escopo:** um plugin (`vendas_ia`) **e uma linha de core** (a correção do §7 abaixo), zero bump de `WHATSBOT_API_VERSION` · **Lado espelho:** `/opt/nexus/checkout/docs/PLANO-CATALOGO-IA.md`
>
> ⚠️ **Correção do cabeçalho original:** este plano dizia "zero linha de core" e o F8 desmente — `_SECRET_KEYS` vive em [db/repositories/audit_repo.py](../db/repositories/audit_repo.py), no core. É uma linha, e conserta a trilha de auditoria de **todos** os plugins de uma vez, não só a deste.
> **Origem:** o Nexus moveu a escrita do catálogo de ofertas do módulo `produtos` para o módulo `checkout`. O `vendas_ia` lê essas tabelas por SQL cru numa segunda conexão read-only e é o consumidor classificado como **Crítico** no levantamento do outro lado.
> **Método:** leitura do código real com `arquivo:linha` conferido nos três repositórios (`whatsbot-pro`, `whatsbot-pro-plugins`, `/opt/nexus/checkout` e `/opt/nexus/produtos`). Nenhuma consulta a banco de produção.
>
> **A boa notícia, que define o tamanho deste plano:** o módulo novo **manteve as quatro tabelas físicas e os mesmos campos**, de propósito e com o motivo escrito no schema dele. Uma varredura em toda a árvore `/home/thiago/whatsbot-pro` confirma que o acoplamento com o Nexus vive **inteiramente** dentro de `plugins/vendas_ia/` — nenhum outro plugin, nenhum arquivo do host. Se nada mais mudasse, a troca de escritor não exigiria uma linha alterada. O plano existe pelo que mudou *em volta*: o formato do `offercode`, o dono do de-para de campanha e o lugar onde o preço agora mora.

---

## 0 — Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| **D1** | O contrato continua sendo **SQL cru read-only**, não HTTP. | O Nexus não tem autenticação de máquina em módulo nenhum: o guard aceita só cookie de sessão. Construir API é projeto próprio e não é pré-requisito de nada aqui. |
| **D2** | O plugin **nunca escreve** no banco do Nexus. Só `connect()`, nunca `begin()`. | Vale para toda fase. Qualquer necessidade de escrita vira pedido ao lado Nexus. |
| **D3** | Mudança de **SQL** propaga com a atualização do plugin; mudança no **SCHEMA das tools** exige re-seed manual. | Separa F1–F6 (baratas) de F7 (cara). Ver §3. |
| **D4** | Nenhuma coluna some da projeção sem conferir os consumidores dos dicts. | `triage.py` lê `key_words`; `ad_match.py` lê `codigo_campanha`; `prompts.py` lê 7 chaves; `filters.py`/`events.py`/`state.py` leem `offercode`. |
| **D5** | Toda consulta nova degrada, nunca levanta. | `run_read` **não** captura exceção ([nexus_db.py:126](../../whatsbot-pro-plugins/plugins/vendas_ia/src/nexus_db.py)): tabela ou permissão ausente derruba a chamada inteira. Consulta a tabela `checkout_*` vai guardada por `to_regclass` ou `LEFT JOIN` tolerante. |
| **D6** | O modo de busca padrão continua `lexica`. | Ligar `hibrida` hoje faria toda oferta criada pelo checkout sumir das três tools, porque o Nexus deixou de sincronizar embeddings. F6 transforma esse gatilho num número visível em vez de uma surpresa. |

**Princípio fixo:** todos os defeitos deste plano falham **em silêncio** — sem exceção, sem log de erro, sem sintoma na tela. Entre "responder menos" e "responder errado", o plugin já escolhe responder menos. O objetivo aqui é fazer o silêncio virar número.

---

## 1 — Resumo executivo

Três coisas quebram **hoje**, sem depender de nenhum corte futuro:

1. **O FAQ da oferta some para toda oferta nascida na tela nova.** O módulo checkout passou a gerar `offercode` como slug (`curso-mikrotik-routeros-v7`), e `resolve_offer_id` decide "isto é um id, não um código" contando se tem hífen. O slug é tratado como se fosse um UUID, o filtro `id_ofertas @> ARRAY[:id]` nunca casa, e sobram só as FAQ com o curinga `*`. A tool responde "Nenhuma pergunta frequente encontrada" — que é uma frase, não um erro.
2. **O lead de anúncio pago vai deixar de resolver oferta.** O de-para código do Meta → oferta virou tabela própria no checkout. A coluna legada continua existindo e continua sendo escrita pela tela antiga, então nada quebrou ainda; mas todo código cadastrado da tela nova em diante é invisível para o `ad_match`.
3. **A IA pode estar entregando o link do checkout da Ticto.** `link_checkout` só é escrito na criação da oferta, e nas ofertas importadas ele aponta para `checkout.ticto.app` de propósito. Basta alguém marcar "Ativa para a IA" numa dessas para o link errado entrar no prompt.

Duas coisas degradam e são baratas de fechar: o plugin não filtra por `active` (só por `is_active_for_ia`), e a busca de cursos por oferta depende de uma coluna denormalizada que guarda **um** vínculo, não todos.

E uma oportunidade grande: todo o preço estruturado — centavos, parcelas, desconto do Pix, prazo de acesso, esgotado, capa — mora em `checkout_ofertas_cobranca`, e a IA lê apenas a frase de marketing derivada dela. A frase é boa (agora é gerada pelo próprio checkout, então já não desalinha do que se cobra), mas não responde "dá para parcelar em 6?" nem "tem desconto no Pix?".

Antes de tudo isso há um **F0 obrigatório**: o build de plugins está travado por um motivo que nada tem a ver com este plano.

---

## 2 — Como funciona hoje (mapa verificado)

### 2.1 A segunda conexão

`nexus_db.py` mantém uma engine SQLAlchemy própria, chaveada pelo DSN, com `sslmode=require` forçado no código (não no DSN) e singleton que se reconstrói quando o DSN muda. Sem DSN, tudo vira no-op logado — o plugin nunca derruba o boot do WhatsBot. `run_read` devolve `list[dict]` e **não** captura exceção.

Quatro consultas de catálogo, todas com cache de 300 s: `fetch_ofertas_ativas` (a lista que alimenta triagem e anúncio), `fetch_oferta_by_offercode` (a OFERTA EM FOCO), `resolve_offer_id` e `counts` (diagnóstico).

### 2.2 As três tools

`search.py` tem doze blocos de SQL: três famílias (ofertas, cursos, FAQ) × três modos (`catalogo`, `lexica`, `hibrida`) + três fallbacks ILIKE. As colunas projetadas estão escritas à mão em `_OFERTA_COLS`/`_CURSO_COLS` e repetidas dentro das CTEs — quatro sítios para a mesma lista.

O código das tools é **cópia congelada no banco**. `seed_tools` só cria a linha se o nome ainda não existe; atualizar o plugin troca só o disco e nunca toca `ai_tools`. Mas cada chamada roda num subprocesso novo que faz `from vendas_ia import nexus_db, search` — então **mudança em `search.py`/`nexus_db.py` vale na chamada seguinte, sem re-seed e sem reiniciar**. O que fica congelado é o dicionário `SCHEMA`: nome, descrição e parâmetros que o LLM vê.

### 2.3 As duas fontes de oferta

`filter.agent.resolve` tenta, nesta ordem: palavra-chave na mensagem do cliente (`triage.match_keyword`) e, se não casar, o clique em anúncio (`_offer_from_ad` → `ad_match.resolve_offer`). As duas leem a mesma lista de `fetch_ofertas_ativas`, e por isso a regra "só fixa oferta ativa para a IA" vale para as duas de graça.

O ramo de anúncio casa `produtos_ofertas.codigo_campanha` contra o código extraído do nome da campanha (`[C045]`). `_codes_of` já aceita a coluna separada por `;` **ou** `,` e já normaliza para caixa alta — o que torna a migração de fonte uma mudança puramente de SQL.

### 2.4 O bloco OFERTA EM FOCO

`prompts.py` injeta no system prompt de todo turno com oferta fixada: nome, offercode, valor, bônus, tempo de acesso, página de vendas e checkout — cada linha só aparece se não estiver vazia. E instrui o LLM a **não** chamar `pesquisar_ofertas` de novo para aquela oferta. Não há segunda chance de corrigir um dado errado ali.

---

## 3 — O que é barato e o que é caro

| Natureza | O que é | Como propaga |
|---|---|---|
| **Só SQL** — trocar tabela/coluna de origem, mudar JOIN, acrescentar filtro, migrar de `codigo_campanha` para `checkout_campanhas`, corrigir `resolve_offer_id` | `nexus_db.py` e `search.py` | Atualizar o plugin basta. Vale na chamada seguinte. |
| **Zona cinzenta** — acrescentar ou remover coluna da projeção | idem | Muda o JSON que o LLM lê **sem declaração formal em lugar nenhum**: o `execute` faz `json.dumps(rows)` cru. |
| **Contrato** — nome, descrição ou parâmetros das tools | `tool_code/*.py` | **Exige re-seed manual**: apagar a tool na tela Tools e semear de novo, ou editar o código lá. |

Ordem de dependência dos arquivos: `_config.py`/`settings.py` (só se surgir setting) → `nexus_db.py` (maior risco: **não tem um único teste**) → `search.py` → `tool_code/*.py` (só se o contrato mudar) → consumidores dos dicts → `routes.py`/`static/config.js` → `tests/` → release.

**Não precisam ser tocados:** `embeddings.py`, `meta_ads.py`, `ad_store.py`, `lifecycle.py` e as três migrations locais do plugin.

---

## 4 — Fases

### F0 · Destravar o build · **obrigatório, antecede tudo**

`scripts/build_plugins.py` valida o catálogo **antes** de selecionar o que construir: `validate_catalog` roda na linha 511, `select_layouts` na 512. Ele exige cobertura total — todo plugin em `plugins/` tem de ter entrada em `catalog.json`. Hoje `plugins/pagamentos` existe no disco e **não** está no catálogo, então qualquer build falha com `BuildError`, inclusive um `--check` de uma linha de SQL do `vendas_ia`.

Nada disso afeta o que já está publicado — o bloqueio é do processo de build.

**Pronto quando:** `python scripts/build_plugins.py vendas_ia --check` sai com código 0.

### F1 · `resolve_offer_id` determinístico — devolve o FAQ da oferta · **quebra**

Hoje: `if "-" in v: return v  # já é um id`. Com offercode-slug, todo código novo é confundido com UUID.

A correção é do lado do WhatsBot, não do Nexus — o módulo de catálogo não deve escolher formato de código público por causa de uma heurística de plugin. Duas formas, ambas de poucas linhas: validar UUID com regex de verdade, ou **inverter a ordem** — tentar `fetch_oferta_by_offercode(v)` primeiro e só tratar como id se não achar. A segunda é preferível: não depende do formato continuar sendo UUID nem do slug continuar tendo hífen, e custa uma consulta cacheada.

**Pronto quando:** um teste a seco com `id_oferta = "curso-mikrotik-routeros-v7"` resolve o id da oferta; e `nexus_db.py` deixa de ser o único arquivo do plugin sem teste.

### F2 · O de-para de campanha passa a ler as duas fontes · **quebra (amanhã)**

Recomendação: **aditivo, sem esperar decisão do outro lado**. `fetch_ofertas_ativas` passa a devolver `codigo_campanha` como a concatenação da coluna legada com os códigos ativos de `checkout_campanhas` daquela oferta, separados por `;`. Como `_codes_of` já faz split em `;`/`,` e normaliza, **nada muda em `ad_match.py`, `filters.py`, `ad_store.py` ou `meta_ads.py`** — a mudança cabe inteira em `nexus_db.py`.

Duas restrições que o desenho tem de respeitar:
- `checkout_campanhas.oferta_id` é **opcional** por desenho (há campanha de produto sem checkout próprio). O join precisa exigir `oferta_id IS NOT NULL`.
- `checkout_campanhas.active` existe e não tem equivalente no lado legado. Filtrar por ele é obrigatório, senão campanha desligada volta a resolver.
- Por D5, a consulta vai guardada: se a tabela não existir (banco de teste antigo), o plugin continua lendo só a coluna legada em vez de derrubar a chamada.

Isto é o que torna o plugin **imune ao corte** do módulo antigo. Sem F2, ele fica preso à coluna legada e quebra no dia em que ela parar de ser escrita.

**Pronto quando:** cadastrar uma campanha só pela tela nova do checkout e ver o lead daquele anúncio resolver a oferta.

### F3 · Filtrar por `active`, não só por `is_active_for_ia` · **degrada**

As duas colunas existem e são escritas pelo checkout; o plugin lê só a segunda. Uma oferta ou produto desativado continua sendo encontrado e oferecido. São seis pontos: `fetch_ofertas_ativas`, `fetch_oferta_by_offercode`, as CTEs de oferta e de curso em `search.py` e os fallbacks.

Não resolve o caso da oferta despublicada da borda que continua ligada para a IA — isso é N3 do lado Nexus —, mas fecha o caso simples e custa um `AND`.

### F4 · Cursos da oferta pela junção real · **degrada**

`search_cursos(offercode=...)` faz `JOIN produtos_ofertas o ON o.id = p.oferta_id`. Essa coluna é legado denormalizado: guarda **um** vínculo, o mais antigo. Num combo, o curso que pertence a cinco ofertas responde só pela primeira — as outras devolvem lista incompleta, sem erro.

A junção de verdade é `produtos_produtos_ofertas`, que ainda traz `access_days` de brinde: o prazo de acesso **daquele produto naquela oferta**, que é a resposta certa para "por quanto tempo eu fico com isso?" quando o combo mistura um curso de 12 meses com um vitalício. Hoje a IA responde por `tempo_acesso`, texto livre no nível da oferta.

### F5 · O preço estruturado chega ao LLM · **oportunidade**

Duas entregas, em ordem de custo-benefício:

**F5a — a OFERTA EM FOCO.** É onde o preço mais importa e o mais barato de enriquecer: uma consulta só, uma oferta só, e o bloco já tem o padrão "só imprime o que não está vazio". Acrescentar preço à vista, desconto do Pix, parcelamento máximo e prazo de acesso estruturado. Zero mudança de contrato de tool.

**F5b — `pesquisar_ofertas`.** Aqui o custo é real: o retorno é `json.dumps(rows)` cru, então cada coluna nova multiplica pelo número de linhas. Com `hybrid_limit = 5` é aceitável; no modo `catalogo` (até 100 linhas) não é. Recomendação: projetar o conjunto enxuto (preço, Pix, parcelas, esgotado) e **não** no modo `catalogo`.

Quatro armadilhas verificadas, que produzem resposta errada se ignoradas:
- **Estado de publicação precisa de duas colunas.** `checkout_publicacoes` guarda `status` e `acao`; uma oferta despublicada fica com `status = PUBLICADO` e `acao = 'remover'`. Ler só o status mostra como no ar o que saiu do ar.
- **Parcela sem cartão.** O checkout só fala em parcelamento quando `CARTAO` está nos métodos. Calcular parcela olhando só `max_parcelas` faria a IA oferecer parcelamento numa oferta só-Pix.
- **Herança da capa.** O checkout usa a imagem do **primeiro** vínculo e devolve nulo se ele não tiver mídia — não procura o próximo com imagem. Um `LIMIT 1 WHERE imagem_url IS NOT NULL` mostraria uma capa que o checkout não mostra.
- **`Decimal` não serializa.** `desconto_pix_pct` é `DECIMAL(5,2)`; o psycopg devolve `Decimal` e o `default=str` do `json.dumps` o entrega ao LLM como a string `"5.00"`. Converter no SQL.

### F6 · Diagnóstico: transformar silêncio em número · **degrada**

Três acréscimos na tela de status, todos baratos e todos sobre defeitos que hoje não têm sintoma:
- **Contagem de ofertas ativas para a IA sem linha de embedding.** É o gatilho de D6: enquanto esse número for maior que zero, ligar `hibrida` faz essas ofertas sumirem. A descrição do setting `search_mode` também precisa dizer isso.
- **`ping()` que testa o que importa.** Hoje ele faz `SELECT 1`, que passa mesmo sem permissão nenhuma nas tabelas do catálogo. Deve testar um `SELECT` real em cada tabela lida — e, depois de F2/F5, também nas `checkout_*`.
- **Não engolir a exceção do SQL ranqueado sem sinalizar.** Hoje a busca cai no fallback ILIKE e loga um `warning`; falta de `GRANT` numa tabela nova ficaria assim para sempre, com a busca pior e ninguém sabendo.

### F7 · Contrato das tools e release · **caro, por último**

Só entra se F1–F6 tiverem mudado algo que o LLM vê. Dois pontos concretos já identificados:
- A descrição do parâmetro `offercode` de `pesquisar_informacoes_cursos` traz o exemplo literal `'OF5540D5F'` — um formato que não existe mais para oferta nova. Mesma coisa em `pesquisar_perguntas_frequentes`, e os prompts semeados dos agentes estão cheios de exemplos hex.
- A descrição de `pesquisar_ofertas` já promete "preços, parcelas e links de pagamento", que só passa a ser verdade depois de F5.

**Atenção ao mecanismo:** editar `tool_code/*.py` **não** atualiza a linha em `ai_tools`. Publicar uma versão nova do plugin não re-semeia. O caminho é manual (apagar e semear de novo pela tela, ou editar o código lá) — e vale a pena avaliar, nesta fase, criar um caminho de atualização versionado em `tools_seed.py`, para que este plano não se repita no próximo.

Release, na ordem do ritual do repositório: `src/plugin.yaml` + `vendas_ia.json` + `catalog.json` → testes verdes → `build_plugins.py`.

### F8 · O DSN do Nexus é um segredo e não está tratado como um · **quebra (segurança)**

`nexus_dsn` é uma setting comum. Duas consequências, as duas do lado WhatsBot:
- O `GET /settings` do plugin devolve o valor **em claro** — `format: password` é inerte, o `PluginSettingsForm` não o lê. O próprio plugin já sabe disso: é exatamente por isso que o token da Meta mora numa rota write-only separada, com máscara.
- `_SECRET_KEYS` do `audit_repo` não inclui `nexus_dsn` (nem `dsn`, `database_url`, `connection_string`), então a senha do banco de produção do Nexus entra em claro na trilha de auditoria e é legível pela tela de Auditoria.

Correções, em ordem de custo: acrescentar as chaves ao `_SECRET_KEYS` (conserta a trilha para **todos** os plugins de uma vez) e mover o DSN para o mesmo padrão write-only já usado pelo token da Meta neste plugin — ou para o padrão do `lms_login`, que já resolveu isto neste repositório.


### F9 · Parar de projetar `lotes` · **degrada**

`lotes` é a única coluna lida pela IA que fica **sem escritor nenhum** quando o módulo antigo
sair: o `checkout` não tem uma linha sequer que a toque (nem no DTO, nem no formulário, nem na
listagem), e hoje ela só é editável na tela de ofertas do `produtos`. O plugin a projeta nas
três buscas de oferta e o resultado vai cru ao LLM.

Depois do desligamento ela vira dado congelado que a IA continua lendo e podendo anunciar.
Duas saídas: tirar da projeção (recomendado — não há consumidor real dela em `prompts.py`,
`triage.py`, `ad_match.py` nem `state.py`), ou pedir ao lado Nexus que a coluna volte a ter
dono. Conferir antes quantas ofertas ativas para a IA têm `lotes` preenchido: se for zero,
a decisão é trivial.

---

## 5 — Desligar o módulo `produtos`: o que o plugin precisa antes

O plugin **nunca falou HTTP com o módulo `produtos`** — não há uma referência a `:8018`,
`/modulo-produtos` ou qualquer rota dele em lugar nenhum da árvore do WhatsBot. Desligar o
contêiner, portanto, não quebra nada por si só. O que quebra é indireto: o módulo antigo ainda
é o **único escritor** de algumas colunas que a IA lê.

A lista é fechada e sai da diferença entre os dois `CreateOfertaDto`:

| Campo só escrito pelo módulo antigo | O que acontece ao desligar | Coberto por |
|---|---|---|
| `codigoCampanha` | perde o escritor; a fonte nova é `checkout_campanhas` | **F2 (obrigatório antes do desligamento)** |
| `lotes` | fica sem escritor nenhum | **F9** |
| `linkCheckout` | deixa de ser editável à mão; o checkout só o calcula no `create` | N1 do lado Nexus |
| `offercode` | imutável no módulo novo — **de propósito**, é o comportamento certo | nada a fazer |
| `valorAtual` | passa a ser derivado do bloco de cobrança | nada a fazer (melhora) |
| `produtoIds` | virou `produtos[]`, com `accessDays` e `position` — superconjunto | nada a fazer |

**Só F2 é bloqueante.** Feito ele, o plugin sobrevive ao desligamento sem mais nenhuma
alteração: FAQ, produtos, ofertas e campanhas têm CRUD completo no módulo novo (o `faq` de lá
tem inclusive o `PUT /reorder`), e as quatro tabelas físicas continuam com os mesmos nomes.

**Três coisas que não são do plugin, mas bloqueiam o desligamento:**

1. **O sync de embeddings mora no módulo antigo.** Desligá-lo mata o único gatilho das três
   `gerenciamento_ia_embeddings_*`. Em `lexica` a busca continua achando pelo lado ILIKE do
   `FULL OUTER JOIN`, perdendo o canal full-text para todo item novo — e `hibrida` fica
   proibido para sempre, porque ligá-lo faria sumir tudo o que o checkout criou depois do
   corte. É o N4 do plano do Nexus, e é o item que decide se `search_mode` volta a ser um botão
   livre ou uma armadilha permanente.
2. **Oferta sem bloco de cobrança é ineditável na tela nova.** Isso tem uma consequência de
   ordem que morde o plano 150: a fase F5 de lá manda **recadastrar as palavras-chave** de toda
   oferta ativa para a IA. Se alguma delas não tiver bloco de cobrança, a tela nova não salva —
   e, com o módulo antigo desligado, não sobra onde editar. **Conferir isto antes de desligar
   qualquer coisa:** quantas ofertas com `is_active_for_ia = true` não têm linha em
   `checkout_ofertas_cobranca`. Se o número não for zero, N6 do lado Nexus vira pré-requisito
   do plano 150, não só do desligamento.
3. **O cron `meta_ads_sync` do Windmill lê a coluna legada.** Desligar o módulo não apaga a
   coluna, então o cron continua funcionando com o que já está lá — mas para de receber código
   novo, exatamente como o plugin pararia sem F2. Windmill e plugin são **um corte só**, não
   dois.

### 5.1 Ordem recomendada

1. F0 (destravar o build) · F2 (campanhas nas duas fontes) · F9 (`lotes`).
2. Conferir a contagem do item 2 acima; se não for zero, N6 antes de seguir.
3. Plano 150 F5 — recadastrar as palavras-chave enquanto **as duas telas** ainda salvam.
4. N4 do lado Nexus (embeddings) — ou aceitar por escrito que `hibrida` está proibido.
5. Migrar o `meta_ads_sync` do Windmill para `checkout_campanhas`.
6. Só então: remover as rotas de escrita do `produtos`, tela em leitura, e o desligamento
   propriamente dito, na sequência do PLANO-FASE-1B §5.

### 5.2 Renomear as tabelas depois — leia antes de planejar

**A renomeação é proibida por decisão registrada do próprio módulo `checkout` (D24 do PLANO.md
de lá), e o `vendas_ia` é um dos cinco consumidores que ela quebraria de uma vez.** Não é uma
consequência do desligamento: é uma operação independente e muito maior.

O que ela custa, medido: doze blocos de SQL em `search.py` e quatro consultas em `nexus_db.py`
citam os nomes literalmente; o cron do Windmill quebra em silêncio; os `GRANT` da role de
leitura são nominais por tabela e viram inválidos; e a BIA (`gerenciamento-ia`) lê as mesmas
tabelas de outro repositório.

Se ainda assim for para acontecer, existe um caminho barato **para este lado**: o plugin só faz
`SELECT`, então uma **view de compatibilidade** com o nome antigo sobre a tabela renomeada o
mantém funcionando sem uma linha alterada — e vale igual para o Windmill e para a BIA. É a
única forma de renomear sem um corte coordenado de quatro consumidores no mesmo dia. Mas a
decisão é do lado Nexus e a D24 continua valendo até que alguém a reabra explicitamente.

---

## 6 — Testes

O único teste que trava SQL é `tests/python/test_search.py`: ele monkeypatcha `run_read` com um gravador e faz assert em fragmentos literais da string (`'is_active_for_ia = true'`, `'active_ia = true'`, `'id_ofertas'`, `"plainto_tsquery('portuguese', :q)"`, os `DISTINCT ON` e os `ORDER BY` exatos). Trava a **forma**, não o resultado — então F2, F3, F4 e F5 vão quebrá-lo, e isso é o sinal certo.

`test_triage_filter.py` e `test_ad_offer.py` congelam só os **nomes das chaves** dos dicts de oferta; qualquer coluna que sumir da projeção aparece ali.

O buraco a fechar em F1: **`nexus_db.py` não tem um único teste**, e os dois testes que passam por `resolve_offer_id` o monkeypatcham — a função nunca é executada em CI.

---

## 7 — Riscos e critérios de aceite

| # | Risco | Mitigação |
|---|---|---|
| R1 | Ler tabela `checkout_*` sem `GRANT`: a busca cai no fallback ILIKE sem ranking, pior que hoje e sem sintoma | F6 estende o `ping`; a fase que introduz a leitura só fecha depois de o `SELECT` funcionar nos dois bancos |
| R2 | Acrescentar colunas demais e estourar o contexto no modo `catalogo` (100 linhas) | F5b projeta o conjunto enxuto e não no modo `catalogo` |
| R3 | Mexer no SCHEMA das tools e esquecer o re-seed — o plugin sobe, o LLM continua com o contrato velho | F7 é fase própria, com o passo manual escrito |
| R4 | O Nexus limpar `produtos_ofertas.codigo_campanha` antes de F2 | F2 é aditiva e não depende do outro lado; a coordenação está registrada em N2/N5 do plano do Nexus |
| R5 | Ligar `search_mode = "hibrida"` enquanto o Nexus não sincroniza embeddings — todo o catálogo novo some | F6 põe o número na tela e o aviso na descrição do setting; a correção definitiva é N4 do lado Nexus |

**Aceite do plano:** uma oferta criada do zero na tela nova do checkout é encontrada pela busca, tem o FAQ dela respondido, resolve o lead do anúncio pelo código de campanha cadastrado na tela nova, e o link e o preço que a IA entrega ao cliente são os mesmos que o checkout cobra.


---

## 7 — Execução (2026-08-29)

Executado **em paralelo com o plano 150**, que estava mexendo em `triage.py`,
`ad_match.py` e `filters.py` no mesmo checkout. As duas frentes não se cruzaram
em nenhum arquivo de lógica; os únicos pontos compartilhados foram
`settings.py`/`_config.py` (regiões diferentes) e o **release**.

### O que foi feito

| Fase | Onde | O que mudou |
|---|---|---|
| F0 | `catalog.json` (repo de plugins) | `pagamentos` entrou no catálogo. Era o que travava **qualquer** build: `validate_catalog` exige cobertura total e roda ANTES de `select_layouts` |
| F1 | `nexus_db.resolve_offer_id` | pergunta o offercode ao banco **antes** de tratar como id — a heurística `if "-" in v` lia todo slug do checkout como UUID |
| F2 | `nexus_db.fetch_ofertas_ativas` | `codigo_campanha` = coluna legada ∪ `checkout_campanhas` ativas, concatenadas com `;`. `ad_match`, `filters`, `ad_store` e `meta_ads` **não mudaram uma linha** |
| F3 | `nexus_db` + `search` (13 sítios) | `AND active = true` ao lado de `is_active_for_ia`/`active_ia` |
| F4 | `search._cursos_da_oferta` | junção real `produtos_produtos_ofertas` + `access_days` traduzido (`0` ⇒ vitalício, `NULL` ⇒ herda a oferta) |
| F5a | `pricing.py` (novo) + `prompts.py` | preço, Pix, parcelas e esgotado no bloco OFERTA EM FOCO |
| F5b | `search._com_preco` | mesmas chaves em `pesquisar_ofertas`, **fora** do modo `catalogo` (R2) |
| F6 | `nexus_db.ping`/`counts`, `routes`, `config.js`, `settings.py` | ping por tabela, ofertas sem embedding, ofertas com link externo, degradação da SQL ranqueada em `ERROR` |
| F7 | `tool_code/*.py` | descrições sem o exemplo hex morto; `pesquisar_ofertas` descreve os campos de preço que agora existem |
| F8 | `_config`, `settings`, `routes`, `config.js` + **core** `audit_repo` | DSN em rota write-only; `nexus_dsn`/`dsn`/`database_url`/`connection_string` mascarados na trilha |

**Decisão de desenho não prevista no plano** — F5b enriquece as linhas **em
Python**, depois da consulta, em vez de acrescentar colunas às CTEs. O SQL de
ofertas repete a lista de colunas em quatro sítios (semântica, ILIKE, full-text e
o `COALESCE` da `ranked`); uma consulta a mais por ids custa menos que acertar os
quatro, e deixa intactos o SQL ranqueado e os testes que travam a forma dele.

**Não implementado de propósito:** leitura de `checkout_publicacoes`. A armadilha
de F5 (`status = PUBLICADO` + `acao = 'remover'` = oferta fora do ar) só existe se
o estado de publicação for surfaçado — e nada no plano pede isso. Evitar a leitura
elimina a classe inteira do erro.

### Testes

`nexus_db.py`, que era o único módulo do plugin **sem um teste sequer**, passou a
ter 24. Mais 13 de `pricing.py` e 13 novos em `test_search.py`. De **89 para 139**.

A regressão de F1 foi verificada com dente: restaurando `if "-" in v: return v`,
`test_offercode_slug_resolve_o_id_da_oferta` e
`test_a_ordem_e_offercode_primeiro_id_depois` falham.

⚠️ **`test_search.py` ganhou um fixture autouse que limpa o cache de `nexus_db`.**
A sondagem de tabela é cacheada por 300 s em estado de MÓDULO — sem limpar, o
resultado de um teste responde pelo seguinte.

### O que faltava, e como fechou (2026-08-31)

1. **F9 · parar de projetar `lotes`.** Tirado das três buscas de oferta
   (`search.py`), com teste de trava — sem ele nada mais acusava a remoção.
   Decisão trivial na prática: **0 das 9 ofertas ativas para a IA** têm `lotes`
   preenchido.

2. **Re-seed manual das tools (F7 · R3) virou re-seed VERSIONADO.** Em vez do
   passo manual "apagar e semear na tela Tools" que o plano previa como
   provisório, `tools_seed.sync_tools()` usa `tool_repo.sync_source()` (que já
   existia no core, sem precisar de mudança nele): publicar versão nova do
   plugin realinha sozinho o código de tool que o operador nunca editou
   (`version <= 1`) e **avisa**, em vez de silenciar, quando a tool foi
   customizada. O passo manual não existe mais daqui pra frente.

3. **Release — era a decisão de UMA, não de duas.** O plano 150 tinha marcado
   `plugin.yaml` em **1.9.0** (palavra-chave exata). As duas frentes editaram o
   MESMO `src/`; o release juntou as duas descrições em **1.10.0** — publicado
   localmente (`storages/plugins/vendas_ia/`, `enabled=1`, `load_error=None`) e
   `build_plugins.py --check` limpo nos 27 plugins do catálogo. **Não foi feito
   `git push`** para o repositório de plugins nem import em produção — decisão
   do operador, não deste plano.

### Testes

179 testes Python (era 89 antes desta frente + do plano 150; `nexus_db.py` saiu
de zero teste para 24, `pricing.py` ganhou 13, `tools_seed.py` e as rotas de
auditoria do §4.1/§F8 têm suíte própria).
