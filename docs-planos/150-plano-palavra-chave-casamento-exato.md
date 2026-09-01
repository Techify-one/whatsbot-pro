# Plano 150 — A palavra-chave da oferta passa a ser casamento EXATO com a mensagem inteira do cliente

> **Status:** **CÓDIGO EXECUTADO em 2026-08-29** (F2/F3/F4 verdes, `vendas_ia` 1.9.0 instalada em `storages/plugins/`) · **Falta F5** (recadastrar as palavras-chave no Nexus — §5) e **F6** (publicar o zip, travado pela F0) · **P1/P2/P3 todos fechados** (§0) · **Escopo:** pequeno — uma função pura em um plugin (`vendas_ia`), **zero linha de core**, zero migração · **Consequência operacional:** grande, ver §5
> **Origem:** pedido do operador. Hoje a palavra `firewall` cadastrada numa oferta faz a IA fixar essa oferta quando o cliente escreve "Quero um curso de firewall". O operador quer **tudo ou nada**: só casa se a mensagem for exatamente a palavra-chave, tolerando apenas maiúscula/minúscula e acento.
> **Reforço de 2026-08-29:** o incidente de 28/08 em produção (§1.3) foi investigado a fundo e **não** foi causado pelo casamento por substring — a palavra-chave estava cadastrada na oferta errada. O incidente **não é o argumento** deste plano; o argumento continua sendo o defeito estrutural da regra `in`. Mas a investigação expôs, no mesmo cadastro, **uma duplicata exata de palavra-chave entre duas ofertas ativas** cujo desempate hoje é a ordem de um `SELECT` sem `ORDER BY` — o caso da §3.3 deixou de ser hipotético.
> **Método:** leitura do código real com `arquivo:linha` conferido no plugin e nos dois módulos do Nexus.
>
> **Achado que muda o enquadramento:** o lado do Nexus **já implementou esta regra no cadastro**. A tela de ofertas do módulo `checkout` ensina, com estas palavras, que a comparação é "exata — tudo ou nada, não é 'contém'", e a documentação daquele módulo registra que isso "é o contrato que o atendimento vai implementar". Este plano não inaugura uma regra: ele **cumpre um contrato já escrito** e fecha uma divergência viva entre o que o cadastro promete e o que o atendimento faz.

---

## 0 — Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| **D1** | O casamento é **exato contra a mensagem inteira**, nunca "contém". | Some o conceito de "a palavra-chave aparece no meio da frase". |
| **D2** | A normalização permitida é **caixa e acento**. Nada de radical, sinônimo, plural ou distância de edição. | `FIREWALL`, `firewall` e `firewáll` casam entre si. `firewalls` **não** casa `firewall`. |
| **D3** | O gatilho continua valendo **só** para ofertas com `is_active_for_ia = true`. | Não muda: a lista vem de `fetch_ofertas_ativas`, que já filtra. |
| **D4** | A mudança é **só do lado WhatsBot**. | O cadastro já está certo. Mexer no Nexus aqui seria reescrever a dica a partir do SQL de hoje — exatamente o que a documentação daquele módulo proíbe. |
| **D5** | O comportamento antigo **não** volta por configuração. | Um "modo contém" opcional preservaria o defeito e criaria dois contratos para a mesma coluna, com o cadastro ensinando um e o runtime fazendo outro. Se for preciso reverter, reverte-se a versão do plugin. |

**Fechadas pelo operador em 2026-08-29** (eram P1 e P2):

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| **D6** | Espaço nas pontas e espaço interno repetido **não contam** (era P1(a)). | Um espaço colado pelo teclado do celular não derruba mais o casamento. Passo 1–2 da normalização, §2. |
| **D7** | Pontuação de fim de frase **não conta** (era P2(a)). | `firewall.` casa `firewall`. É a decisão mais discutível do plano — está aqui, à vista, e não escondida no código. Passo 3 da normalização, §2. |

**Fechada na execução de 2026-08-29** (era P3):

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| **D8** | O fallback por texto do anúncio é **aposentado** (alternativa (a)). | `offer_by_ad_text`, o ramo `require_code=False` de `resolve_offer`, o rótulo `ad:texto:<kw>` e a setting `ad_require_campaign_code` foram REMOVIDOS. Motivo: ele chamava `triage.match_keyword`, então sob a regra exata **nunca mais casaria nada** — o corpo de um anúncio jamais é igual, inteiro, a uma palavra-chave. Deixá-lo desligado seria manter no código uma promessa que o comportamento não cumpre. Já estava off por padrão e errou nas 34 vezes em que agiu em produção. **Reverter é reverter a versão do plugin** (D5), não religar uma chave. |

---

## 1 — Como funciona hoje (mapa verificado)

O gatilho é `triage.match_keyword`, chamado por `filter.agent.resolve`:

- A mensagem vira `text.lower()`.
- Para cada oferta ativa, `key_words` é dividida por `;`.
- Cada pedaço é testado com `kw.lower() in msg` — **substring**.
- Entre todos os casamentos, a palavra-chave **mais longa** vence; empate de comprimento mantém a primeira encontrada.

Consequências verificadas:

1. **Casa no meio da frase.** É o pedido do operador: `firewall` casa "Quero um curso de firewall".
2. **Casa dentro de outra palavra.** `firo` casaria "confiro". Nada no código impede.
3. **Não trata acento.** Só `.lower()`. Hoje `segurança` **não** casa "seguranca" — a regra é frouxa onde não devia e rígida onde devia ser tolerante.
4. **O desempate por comprimento existe por causa do defeito.** Com casamento exato, no máximo um pedaço de texto pode casar a mensagem inteira; o desempate deixa de ter função de ranking e passa a significar outra coisa (ver §3.3).

Quem consome o resultado: no acerto, o filtro fixa a oferta na conversa (`state.set_offer`, com `matched_keyword` na trilha), vincula o agente comercial e reconstrói o spec do turno — o cliente é atendido pelo comercial já com a oferta em foco, pulando o roteador.

**O texto avaliado é a última mensagem do usuário daquela conversa**, lida no próprio turno. Isso importa para a §5: o casamento exato só é utilizável quando a mensagem é previsível, e é exatamente o caso do botão "Enviar mensagem" de anúncio e dos links `wa.me?text=…`, que chegam com um texto fixo, idêntico byte a byte.

### 1.1 O segundo consumidor da mesma função

`ad_match.offer_by_ad_text` reusa `triage.match_keyword` para casar a palavra-chave da oferta no `headline + body` do anúncio. Esse caminho está **desligado por padrão** (`ad_require_campaign_code = True`) porque, em produção, atribuiu a oferta errada em **34 de 34 disparos** — sempre a mesma oferta, o COMBO DE SEGURANÇA, cujas palavras-chave são `segurança` e `firewall`, que aparecem no corpo de quase todo anúncio de redes. Uma campanha de captação mandou 32 leads ao comercial com a oferta errada por conter "Mikrotik v7: Firewall, VPN, BGP" no corpo.

Ou seja: **o incidente que motivou a correção do ramo de anúncio é o mesmo defeito que este plano corrige no ramo da mensagem.** Lá foi resolvido desligando o caminho; aqui é resolvido apertando a regra.

### 1.2 O que o Nexus já diz

A tela de oferta do módulo `checkout` (`/opt/nexus/checkout/src/app/ofertas/formulario.tsx:670-690`) instrui o operador:

> "Cada uma é a **mensagem inteira** que o cliente manda ao chegar no atendimento (…) A comparação é **exata — tudo ou nada**, não é 'contém'. Uma palavra a mais ou a menos e não vale. Por isso não cadastre termo solto: 'firewall' **não** pega 'Olá, tenho interesse no curso de firewall'."

E o placeholder do campo já é uma frase inteira, não um termo: *"Ex.: Quero informações sobre o Combo de Segurança"*.

A documentação daquele módulo (`/opt/nexus/checkout/docs/AI.md:474`) fecha: *"É o contrato que o atendimento vai implementar (…) O `gerenciamento-ia` ainda casa por `key_words ILIKE '%termo%'`; a diferença é de lá."*

Existem, portanto, **três implementações da mesma coluna** e nenhuma delas exata: o cadastro (que já promete exato), este plugin (`in`), e a BIA/`gerenciamento-ia` (`ILIKE '%termo%'`). Este plano cobre a segunda. A terceira fica registrada como pendência de outro repositório, que não está nesta máquina.

### 1.3 O incidente de 28/08 — o que ele prova e o que ele não prova

Às 17:43 de 28/08 o operador testou, no canal Atendimento, a mensagem exata de um anúncio:
`Olá! Tenho interesse no Combo de Redes para Empresas.` A IA não apresentou a oferta e o
cliente foi transferido para humano em 24 segundos.

**A investigação (execução 14104, conversa 16322) mostrou que o gatilho funcionou inteiro:**

| Instante | O que aconteceu | Prova |
|---|---|---|
| 17:43:05.636 | `filter.agent.resolve` casa a frase, fixa a oferta e faz `set_agent('comercial')` | `plugin_vendas_ia_conversa` conv 16322: `matched_keyword` = a frase inteira |
| 17:43:05.665 | O bloco "OFERTA EM FOCO" entra no system prompt — **com a oferta errada** | captura do `debug_bus` id 1404924: *"Oferta: Curso MikroTik para Provedores · offercode: O0682ECD4"* |
| 17:43:05.683 | 1º hop do LLM roda com `agent_key='comercial'` | `execution_steps` 60390, `executions.routing_steps` = `[{"from":"comercial","to":"roteador","depth":1}]` |
| 17:43:12–14 | O **comercial** chama `pesquisar_ofertas` (vazio) e devolve ao roteador | `execution_steps` 60391/60392, ambos `agent_key='comercial'` |
| 17:43:18 | O roteador chama `transfer_to_human` | `execution_steps` 60395 |

Ou seja: **casamento por substring não teve nenhum papel no desfecho** — a frase inteira foi a
única palavra-chave ativa a casar, e ganharia igualmente sob a regra exata deste plano.

A causa raiz é de **dado**, e a trilha de webhooks do Nexus a reconstrói minuto a minuto
(`RBNexusDB.webhook_logs`, 5 eventos `oferta.*` em 28/08):

- **18:42:19Z** — criada uma oferta nova, *"Combo de Redes para Provedores"*, offercode
  `OAAEA7095ddd` (sufixo digitado errado), com `keyWords` = a frase do anúncio e `codigoCampanha` C050.
- **18:42:58Z** — apagada, 39 segundos depois.
- **18:44:47Z** — o **mesmo conteúdo** aplicado por `UPDATE` sobre a oferta já existente
  `O0682ECD4` *"Curso MikroTik para Provedores"*, que passou a carregar a frase do anúncio de **Empresas**.

E a oferta que o texto promete — *"Combo de Redes para Empresas (Oferta Fecha Mês 08/2026)"*,
`O42433B9D` — está com `is_active_for_ia = false`, logo invisível para **todas** as consultas
do plugin. Foi por isso que `pesquisar_ofertas` respondeu, corretamente, "não existe".

**Por que isto está neste plano:** não como justificativa (a regra exata não teria evitado o
desfecho), mas porque a investigação encontrou, no mesmo cadastro, o caso da §3.3 **vivo**:
`monitoramento` está cadastrada em **duas** ofertas ativas ao mesmo tempo (SCRIPTS DE FAILOVER
E LOADBALANCE `O06C57F42` e COMBO DE MONITORAMENTO `OF5540D5F`). Hoje o desempate é a ordem de
varredura de um `SELECT` sem `ORDER BY` — pode fixar uma oferta ou a outra entre dois boots, sem
nada mudar no cadastro.

---

## 2 — A regra nova, escrita por extenso

Uma mensagem casa uma palavra-chave quando, **depois de normalizadas as duas do mesmo jeito**, elas são a mesma string.

Normalizar significa, nesta ordem:
1. remover espaços das pontas;
2. colapsar espaço interno repetido em um só (P1);
3. remover pontuação de fim de frase (P2);
4. remover os acentos (decomposição Unicode, descartando os sinais combinantes);
5. baixar a caixa.

Nada mais. Não há truncamento, não há remoção de artigo, não há sinônimo, não há tolerância a erro de digitação.

Exemplos com a palavra-chave `firewall` cadastrada:

| Mensagem do cliente | Antes | Depois |
|---|---|---|
| `firewall` | casa | **casa** |
| `FIREWALL` | casa | **casa** |
| `  Firewall  ` | casa | **casa** (P1) |
| `firewall.` | casa | **casa** (P2) |
| `Quero um curso de firewall` | casa | **não casa** |
| `firewalls` | casa | **não casa** |
| `confiro` (com a chave `firo`) | casa | **não casa** |

E com a palavra-chave `Quero informações sobre o Combo de Segurança`:

| Mensagem do cliente | Depois |
|---|---|
| `Quero informacoes sobre o Combo de Seguranca` | **casa** (acento, D2) |
| `quero informações sobre o combo de segurança!` | **casa** (caixa + P2) |
| `Oi, quero informações sobre o Combo de Segurança` | **não casa** |

---

## 3 — Inventário das mudanças

### 3.1 `src/triage.py` — o coração

`match_keyword` deixa de varrer procurando substring e passa a: normalizar a mensagem uma vez; normalizar cada palavra-chave; comparar por igualdade. Sai o acumulador "a mais longa vence".

Uma função `_normalizar(texto)` privada, usada nos dois lados da comparação — nunca duas normalizações diferentes, que é como este tipo de regra apodrece.

### 3.2 `src/ad_match.py` — o segundo consumidor

Com P3 = (a): remover `offer_by_ad_text`, o ramo `require_code=False` de `resolve_offer` e o rótulo de trilha `ad:texto:<kw>`. A setting `ad_require_campaign_code` perde a razão de existir e sai junto — deixá-la ligada prometendo um comportamento que o código não entrega mais é pior do que removê-la.

Com P3 = (b): `offer_by_ad_text` ganha função própria de casamento por substring, e o comentário passa a dizer que ela é deliberadamente diferente da do gatilho de mensagem. Não recomendado.

### 3.3 Palavras-chave duplicadas entre ofertas

Com "contém", duas ofertas com a mesma palavra-chave eram resolvidas pelo desempate por comprimento — que dava uma resposta, mesmo que arbitrária. Com casamento exato vira **empate real**: duas ofertas ativas com a chave `firewall` e uma mensagem `firewall`.

Regra: a primeira encontrada vence (mantém o comportamento atual e é determinística dentro de um mesmo resultado de consulta), **e o empate é logado em nível de aviso**, nomeando as duas ofertas. Hoje isso é invisível; o log é o que torna o cadastro duplicado descobrível.

**Fechado em 2026-08-29:** o operador vai fazer o **Nexus recusar no cadastro** palavra-chave
duplicada e palavra-chave contida em outra. Portanto este plano **não** implementa bloqueio do
lado WhatsBot — mas **mantém o log de aviso do empate**, por dois motivos: ele cobre as linhas que
já estão no banco desde antes da validação nova, e o plugin não pode depender de uma garantia que
mora em outro produto e outro banco. O log é rede de segurança, não regra.

Caso vivo hoje, encontrado em 28/08: `monitoramento` está em `O06C57F42` **e** em `OF5540D5F`,
ambas ativas para a IA. Sob a regra de hoje o desempate é arbitrário; sob a regra nova é um empate
real e o log passa a nomear as duas ofertas.

### 3.4 O que **não** muda

- `filters.on_resolve_agent`: o gate de "spoke já assumiu", a guarda de "IA no comando", a fixação da oferta e a troca de agente ficam intactos.
- `nexus_db.fetch_ofertas_ativas`: continua trazendo `key_words` do mesmo jeito.
- O ramo de anúncio **por código de campanha**: é a fonte determinística e não passa por `match_keyword`.
- As três tools de busca: `key_words` continua entrando no *haystack* da busca textual em `search.py`, e ali "contém" é o comportamento certo — busca é outra coisa, não é gatilho.
- O SCHEMA das tools no banco: nada aqui muda o contrato visto pelo LLM, então **não há re-seed manual** nesta mudança.

### 3.5 Testes

`tests/python/test_triage_filter.py` tem seis testes puros de `match_keyword`; **quatro afirmam o comportamento de substring** e vão quebrar — é o sinal certo, não regressão:

- `test_match_substring_case_insensitive` — "quero info de COMBO26RB agora" deixa de casar.
- `test_match_semicolon_split_second_alternative` — "me fala do plano anual" deixa de casar.
- `test_match_longest_keyword_wins` — o desempate por comprimento deixa de existir.
- `test_match_length_tie_keeps_first` — vira o teste do empate real da §3.3.

Continuam válidos os dois de ausência (`test_match_no_hit_and_empty`, `test_match_ignores_blank_keywords`), e os testes de filtro/anúncio que monkeypatcham a lista de ofertas.

Casos novos a cobrir: igualdade exata; acento nos dois sentidos; caixa; espaço nas pontas e duplo (P1); pontuação final (P2); a mensagem contendo a chave **não** casando; a chave contendo a mensagem **não** casando; empate entre duas ofertas.

---

## 4 — Fases

| Fase | O quê | Pronto quando | Estado |
|---|---|---|---|
| **F0** | Destravar o build — `plugins/pagamentos` não está em `catalog.json` e qualquer build falha antes de olhar o `vendas_ia` (detalhe no plano 149, F0) | `build_plugins.py vendas_ia --check` sai com 0 | **BLOQUEADA** — segue dando `catalogue coverage mismatch (missing from catalogue: pagamentos)`. Publicar (ou não) o `pagamentos` é decisão do plano dele, não deste |
| **F1** | Fechar **P3** com o operador (P1 e P2 fechados em 2026-08-29 → D6/D7) | A resposta registrada em §0 | ✅ **D8** |
| **F2** | `_normalizar` + `match_keyword` exato em `triage.py`; log de empate | Testes novos verdes | ✅ |
| **F3** | P3 aplicado em `ad_match.py` (e a setting removida, se for (a)) | `ad_offer_enabled` continua funcionando pelo código de campanha; nenhuma referência órfã a `offer_by_ad_text` | ✅ — `_config.py`, `settings.py` e `filters.py` também limpos |
| **F4** | Reescrever os quatro testes e acrescentar os oito casos novos | Suíte do plugin verde | ✅ **89 passando** (`scripts/test_plugins.py vendas_ia`) |
| **F5** | **Recadastrar as palavras-chave em produção** — não é código, e a lista fechada das 7 ofertas está na §5 | Nenhuma oferta ativa para a IA tem palavra-chave de termo solto; a duplicata `monitoramento` resolvida | ⏳ **do operador** — o catálogo conferido em 2026-08-29 está igual ao da §5 |
| **F6** | Documentação: atualizar a descrição da setting "Habilitar palavra-chave → oferta" para dizer que o casamento é exato; entrada do `vendas_ia` no README do repositório de plugins; release (`plugin.yaml` + `vendas_ia.json` + `catalog.json`) | Publicado | 🟡 **parcial** — descrição da setting, README, `plugin.yaml`, `vendas_ia.json` e `catalog.json` já em **1.9.0**; o **zip não foi gerado** (F0) e nada foi commitado |

**Onde o código está agora:** fonte em `whatsbot-pro-plugins/plugins/vendas_ia/src/` e cópia
**instalada e rodando** em `storages/plugins/vendas_ia/` (idênticas). O `.zip` publicado continua
na 1.8.0 — quem instalar por `Importar (.zip)` ainda recebe a regra antiga.

**A ordem de F5 é deliberada e não pode ser invertida:** subir o código antes de recadastrar deixa o gatilho praticamente sem efeito por um período. Subir depois de recadastrar deixa as frases inteiras casando por "contém" — que continua funcionando. **Recadastrar primeiro é o único dos dois que não tem janela ruim.**

---

## 5 — A consequência operacional, dita sem rodeio

As palavras-chave que existem hoje em produção são, na maioria, **termos soltos**. Depois desta
mudança elas **praticamente nunca vão casar** — ninguém chega no atendimento escrevendo só "ipv6".

Inventário fechado em 2026-08-29 — as **9** ofertas com `is_active_for_ia = true` são o universo
inteiro do gatilho, e **7 delas quebram**:

| Oferta | offercode | `key_words` hoje | Depois |
|---|---|---|---|
| Curso MikroTik para Provedores | `O0682ECD4` | `Olá! Tenho interesse no Combo de Redes para Empresas.` | **continua casando** — é frase de chegada. Mas está na oferta **errada** (§1.3): corrigir o dono antes de qualquer coisa |
| COMBO DE SEGURANÇA | `OCEC96548` | `Quero informações sobre o Combo de segurança` | **continua casando** |
| Workshop: Renda Extra Com Serviços de Redes | `O2289E08D` | `Como Prestar Serviços em Redes de Computadores;como precificar;como encontrar clientes;Fazer Renda Extra Com Serviços de Redes` | quebra — 4 termos, nenhum é mensagem de chegada |
| Scripts de Zabbix + Grafana | `O6A9890D4` | `scripts zabbix + grafana;scripts zabbix;scripts grafana;scripts zabbix e grafana` | quebra |
| COMBO DE IPV6 MIKROTIK | `O3C5524CE` | `ipv6` | quebra |
| Combo de Roteamento | `O384977EA` | `combo de roteamento` | quebra |
| SCRIPTS DE FAILOVER E LOADBALANCE | `O06C57F42` | `scripts;failover e loadbalance;monitoramento;failover de links` | quebra — e `monitoramento` colide com a linha de baixo |
| COMBO DE MONITORAMENTO | `OF5540D5F` | `zabbix;monitoramento;linux;snmp;combo de monitoramento` | quebra — `zabbix` também está contida nas chaves de `O6A9890D4` |
| CURSO MIKROTIK V7 | `O2F2C6561` | `mikrotik v7` | quebra |

**Conferido de novo em 2026-08-29, rodando o matcher NOVO contra o catálogo real** (as 9
linhas acima seguem idênticas). Além da duplicata já conhecida, o cadastro tem **continências
CRUZADAS entre ofertas diferentes** — que é justamente o segundo caso que o Nexus vai passar a
recusar, e que hoje decide oferta pelo desempate por comprimento:

| Chave | Está em | Contida na chave | Que está em |
|---|---|---|---|
| `monitoramento` | `OF5540D5F` **e** `O06C57F42` | — (é DUPLICATA exata) | — |
| `zabbix` | `OF5540D5F` | `scripts zabbix`, `scripts zabbix + grafana`, `scripts zabbix e grafana` | `O6A9890D4` |
| `scripts` | `O06C57F42` | `scripts zabbix`, `scripts grafana`, `scripts zabbix + grafana`, `scripts zabbix e grafana` | `O6A9890D4` |

Sob a regra nova nenhuma delas casa mais nada (não são mensagens de chegada), então a
continência deixa de causar dano — mas ela mostra que **F5 não é "encompridar as frases", é
redesenhar o cadastro**: `scripts` e `zabbix` hoje decidem entre DUAS ofertas por acidente de
comprimento.

Duas observações que mudam o tamanho do trabalho de F5:

- **O recadastro já começou.** As duas linhas que "continuam casando" foram editadas em 28/08 e
  já estão no formato de frase inteira. F5 não inaugura uma prática; termina uma migração em curso.
- **Quatro ofertas têm `key_words` preenchida e `is_active_for_ia = false`** — `OEEE316AC`
  (COMBO DE REDES, com `COMBO26RB`), `O22350111`, `O5428A72F` e a de homologação. Elas **já** não
  casam nada hoje, sem erro nem log, e o cadastro parece pronto. Existe anúncio ativo cuja frase é
  literalmente *"Quero informações sobre o Combo de Redes. COD PROMOCIONAL: COMBO26RB"* — 9 conversas
  em 30 dias, **zero** casadas. Isso **não** é causado por este plano e **não** é corrigido por ele
  (§7), mas atrapalha a leitura do "antes e depois": parte do gatilho já está mudo hoje.

Isso não é efeito colateral: é o objetivo. O gatilho deixa de ser "adivinhar a oferta pelo assunto da frase" e passa a ser "reconhecer uma mensagem combinada". Quem chega com uma pergunta em linguagem natural passa a cair no roteador, que cumprimenta e pergunta o que a pessoa procura — e a oferta é encontrada pela busca, que é a ferramenta feita para isso e que continua casando por "contém".

Para o gatilho continuar tendo função, as palavras-chave precisam ser **recadastradas como a mensagem inteira que se espera receber**, que é o que o campo do checkout já pede no placeholder. Os casos em que isso funciona de verdade são os de mensagem previsível:

- o botão "Enviar mensagem" de um anúncio Click-to-WhatsApp, que chega com texto fixo;
- links `wa.me?text=…` em página de vendas, e-mail ou bio;
- QR code de material impresso.

Nesses três, a mensagem chega **idêntica byte a byte**, e o casamento exato é 100 % confiável — enquanto o "contém" era 100 % confiável e também disparava em tudo o mais.

Expectativa honesta a registrar antes de executar: **o número de ofertas fixadas por palavra-chave vai cair, e deve cair.** O termômetro de que a mudança está funcionando não é "continuou fixando tanto quanto antes", é "parou de fixar oferta errada". A comparação vale a pena ser medida antes e depois, pelo campo `matched_keyword` que a trilha já grava.

---

## 6 — Riscos

| # | Risco | Mitigação |
|---|---|---|
| R1 | Subir o código antes de recadastrar e o gatilho ficar mudo | F5 antes de F2/F6; a ordem está escrita e justificada |
| R2 | Normalizar de dois jeitos diferentes nos dois lados da comparação | Uma função só, usada nos dois lados; teste que compara chave e mensagem idênticas com acentos diferentes |
| R3 | P2 = (a) mascarar um cadastro com pontuação sobrando | O log de acerto já grava a `matched_keyword`; a normalização é a mesma dos dois lados, então nunca "quase casa" |
| R4 | A BIA (`gerenciamento-ia`) continuar com `ILIKE '%termo%'` sobre a mesma coluna, com o operador achando que a regra é única | Registrado em §1.2 como pendência de outro repositório; não bloqueia, mas precisa ser dito ao dono daquele módulo |
| R5 | Alguém religar o comportamento antigo por configuração | D5: não existe configuração. Reverter é reverter a versão do plugin |

---

## 7 — O que a investigação de 28/08 achou e este plano **não** corrige

Registrado aqui para não se perder, com a severidade que o cético confirmou. Nada disto bloqueia
o plano 150; cada item é trabalho próprio.

| # | Defeito | Onde | Por que fica de fora |
|---|---|---|---|
| **V1** | **Argumento vazio vira curinga `'%%'` e a busca por nome para de discriminar.** `{"ck": f"%{course_name}%", "on": f"%{offer_name}%"}` sobre `WHERE … (key_words ILIKE :ck OR name ILIKE :on)`: se o LLM preenche só um dos dois — o uso normal, já que o schema apresenta os dois como modos alternativos — o outro vira `'%%'`, o `OR` colapsa em verdadeiro e a tool devolve **as 9 ofertas ativas** como se fossem resultado de busca. Já ocorreu 2× em produção em 28/08. **Severidade alta.** | `storages/plugins/vendas_ia/search.py:141-144` | É a tool de busca, não o gatilho de palavra-chave. Corrigir junto misturaria duas mudanças de risco diferente numa release só |
| **V2** | **Os cards de `tool_call` do painel são carimbados com o agente FINAL do turno**, não com quem executou a tool. Foi isto que fez as tools do BIA Comercial aparecerem assinadas "BIA Triagem" e induziu, na leitura inicial do incidente, a conclusão errada de que o comercial nunca assumiu. O `agent_key` por chamada **existe** (acumulado por hop em `_run_hop`) e está sendo descartado. **Severidade média.** | `app/services/agent_run_service.py:390` e `:418` → `app/services/messaging_service.py:1012` | É core, não plugin — outro escopo, outra revisão |
| **V3** | **Oferta com `key_words` preenchida e `is_active_for_ia = false` é invisível em silêncio.** Sem erro, sem log: quem cadastra vê a chave salva e conclui que ligou o gatilho. 4 ofertas hoje, uma delas alvo de anúncio ativo com 9 conversas em 30 dias e zero casamentos | `storages/plugins/vendas_ia/nexus_db.py`, `fetch_ofertas_ativas()` | É diagnóstico de cadastro. O lugar natural é a tela de diagnóstico do plugin, ou o próprio Nexus na validação nova |
| **V4** | **`plugin_vendas_ia_conversa` é UPSERT por `conversation_id`, sem trilha.** Uma conversa fixada em X e depois em Y só guarda Y, então qualquer contagem tirada dessa tabela responde "quantas **estão** fixadas", nunca "quantas **já foram**" | `storages/plugins/vendas_ia/state.py`, `set_offer()` | Importa para **medir** o antes-e-depois pedido na §5 — a medição precisa vir de `matched_keyword` observado ao longo do tempo, não de um retrato da tabela |
| **V5** | O fragmento "OFERTA EM FOCO" consulta o Nexus **sem cache**, no thread do event loop, uma vez por hop de routing | `storages/plugins/vendas_ia/prompts.py` | Desempenho, severidade baixa |

**Um alerta que vale mais que os cinco:** o Nexus tem `webhook_logs` (o payload do webhook de
saída) como única memória de alteração de oferta, e ela **não grava autor** — `logs.user_id` e
`logs.ip` são `NULL` em todas as linhas do módulo `produtos`, e `produtos_ofertas` não tem coluna
de autoria. Pior: a trilha só existe **se o webhook disparar**, e as 276 entregas registradas
apontam todas para uma URL de `webhook.site` que devolve 404. A alteração das 20:04:28Z em
`O0682ECD4` não deixou linha nenhuma. Foi por sorte que a causa raiz do incidente de 28/08 pôde
ser reconstruída — **da próxima vez pode não dar.**
