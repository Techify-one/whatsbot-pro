# Plano 105 — Campo do protocolo como ATALHO no "Resolver atendimento" + VALOR PADRÃO por campo

> **Status:** PLANEJAMENTO · **Data:** 2026-08-05 · **Escopo:** médio (plugin `protocolos` inteiro — backend + 2 telas + testes; **zero mudança no core**)
> **Origem:** pedido do usuário — *"quero criar um campo Temperatura (frio/morno/quente) e que os atendentes atualizem ele ao fechar cada atendimento, sem ter que ir na engrenagem procurar o protocolo da pessoa"* + *"poder definir um valor padrão para os campos"*.
> **Método:** leitura do código real da fonte em `../../whatsbot-pro-plugins/plugins/protocolos/src/`, com `arquivo:linha` verificado em `logic.py`, `routes.py`, `static/*.js` e nas migrations 003/004. A 1ª redação foi feita sobre a **1.26.0**; a base hoje é a **1.26.1** (ver a ATUALIZAÇÃO abaixo). ✅ Todas as âncoras `arquivo:linha` de `logic.py` **continuam válidas**: o arquivo é byte-idêntico entre 1.26.0, o zip de produção 1.26.1 e a cópia instalada (`md5 1af91115…` nos três) — a subida de versão não moveu nenhuma linha citada.
> Um campo do escopo **protocolo** ganha a marcação "mostrar ao resolver atendimento": ele aparece numa seção própria do popup de resolver, mas o **valor continua sendo do protocolo** (`plugin_protocolos_protocolo_extras`) — é só um atalho de digitação. Em paralelo, todo campo personalizado ganha **valor padrão**, e protocolo novo já nasce com ele gravado.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.
>
> ---
>
> ⚠️ **ATUALIZAÇÃO (2026-08-05, depois da 1ª redação — LEIA ANTES DE EXECUTAR)**
> A base mudou de **1.26.0 para 1.26.1** e apareceu um módulo que a 1ª redação deste plano ignorava
> por completo: **[`src/retornos_fields.py`](../../whatsbot-pro-plugins/plugins/protocolos/src/retornos_fields.py)**.
> Ele publica cada campo configurável do protocolo como **condição de regra** do plugin `retornos`, lendo
> `get_field_defs(scope)` (definições) e `entidade["fields"]` (valores). Consequências que **mudam este plano**:
> 1. As 2 chaves novas da F1 são **transparentes** para ele (só lê `type`/`key`/`label`/`options`) — sem conflito.
> 2. A **F3 e a F6 conflitam de verdade**: materializar o padrão e proibir o vazio matam a condição
>    *"está vazio"* de réguas de follow-up JÁ SALVAS. Ver §5 e a nova consequência da D6.
> 3. Duas superfícies que a 1ª redação deu como "não afetadas" **são afetadas**: o **arrastar para "Sem valor"**
>    do Kanban (§2 #21, P6) e o **cache do Kanban no nascimento** (§2 #22, critério da F3).
> 4. A F7 ganhou um item **obrigatório** de guarda: o build 1.26.0 saiu sem esse módulo — foi essa a regressão
>    que a 1.26.1 consertou, e um rebuild descuidado a reintroduz.
>
> **Estado da fonte quando esta atualização foi escrita:** `src/` já está em **1.26.1** (port de produção aplicado:
> `retornos_fields.py` + `filters.py` + `plugin.yaml`, byte-idênticos ao zip de produção) e o plano **já está sendo
> executado por outra sessão** — `logic.py`, `routes.py` e `tests/python/test_field_default_and_shortcut.py` estão
> sob edição concorrente. Quem executar: **confira o estado real antes de aplicar qualquer item.**
>
> ⚠️ **Deriva de linha:** todas as âncoras `logic.py:<n>` deste plano valem para o **HEAD commitado** (1.26.1),
> e foram conferidas uma a uma. A execução em curso já cresceu o arquivo em ~139 linhas, então **no working tree
> os números estão deslocados** (ex.: `d["fields"] = extras` estava em 592 e já está em 703). Ao seguir uma âncora,
> procure o **símbolo** citado, não a linha — ou use `git show HEAD:plugins/protocolos/src/logic.py`.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| D1 ✅ (2026-08-05) | O campo exibido no popup **continua pertencendo ao protocolo**. No banco nada muda de dono | Grava em `plugin_protocolos_protocolo_extras`; **não** vira campo de `atendimento`, **não** é espelhado em `conversations.custom_attributes` |
| D2 ✅ (2026-08-05) | No popup ele aparece em **seção separada com título "Protocolo"** | Resolve de quebra o homônimo: "Resultado" existe nos DOIS escopos (§2 ⚠️ #12) |
| D3 ✅ (2026-08-05) | O campo-atalho é **semeado com o valor ATUAL do protocolo** | Exceção consciente à regra "o popup nasce em branco" da 1.23.0 — ver §2 #8 |
| D4 ✅ (2026-08-05) | Valor padrão: **protocolo NOVO nasce com o valor já gravado** (Kanban abre na coluna certa) | Materialização em `ensure_protocolo_for_contact`; **sem backfill** |
| D5 ✅ (2026-08-05) | Os protocolos que já existem **continuam vazios** | Nada de semear default em entidade existente. O usuário vai preencher por fora ("uma IA no fim de semana"). O que está **fechado, fica como está** |
| D6 ✅ (2026-08-05) | Campo com padrão **não oferece opção vazia em NENHUM formulário** (popup + modal do protocolo) | "Limpar seleção" passa a significar **voltar ao padrão**, nunca esvaziar. ⚠️ **Consequência descoberta depois (2026-08-05):** somada à D4, o campo fica **impossível de esvaziar por formulário** — e o construtor de regras do plugin `retornos` expõe *"está vazio"* como operador sobre esse mesmo campo ([retornos_fields.py:187](../../whatsbot-pro-plugins/plugins/protocolos/src/retornos_fields.py#L187)). Uma decisão de UX do popup **apaga uma condição de regra** do motor de follow-up. A única saída que sobra para o vazio é o arrastar do Kanban — ver **P6** |
| D7 ✅ (2026-08-05) | **Nenhuma trava nova** de obrigatoriedade no botão "Resolver" | O `required` do escopo protocolo continua sendo gate só do **Finalizar protocolo** ([logic.py:1120](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L1120)); o `_check_before_status` ([logic.py:4706](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L4706)) **não** é tocado |
| D8 ✅ (2026-08-05) | Mão única: campo de **protocolo → popup de resolver**. O contrário fica fora | Só o escopo `protocolo` ganha a marcação de atalho; o **valor padrão** vale para os dois escopos |

**Princípio fixo do repo aplicado aqui:** *tudo que puder ir para o plugin vai SÓ para o plugin* (`CLAUDE.md` → "O que fica no core e o que vai pro plugin"). Este plano **não toca em nenhum arquivo do core** — inclusive a regra D6, que poderia ser um `clearable` novo no `OptionListSelect` do core e **não** será (ver §3 "Falsos positivos", linha 1).

---

## 1. Resumo executivo

O plugin tem **dois escopos de campo totalmente separados** ([logic.py:52](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L52)): `protocolo` (modal "Finalizar protocolo") e `atendimento` (popup "Resolver atendimento"). Hoje não há passagem entre eles, e para tocar um campo do protocolo o atendente precisa sair da conversa, abrir a aba Protocolos e caçar o protocolo do contato.

A boa notícia é que o `POST /atendimentos/{id}/resolve` **já resolve o protocolo aberto do contato** — `resolve_atendimento` chama `ensure_protocolo_for_contact` e tem o `at["id"]` em mãos ([logic.py:3004](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L3004)) — e `upsert_extra` já é **genérico por escopo** ([logic.py:504](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L504)). Então gravar um extra de **protocolo** dentro da resolução do **atendimento** é acrescentar um parâmetro e um laço, na mesma transação, sem rota nova de escrita e sem tocar no core.

A segunda parte (valor padrão) é uma chave nova na definição do campo. O ponto sutil é **quando** ela vira dado: no escopo `protocolo` é gravada no nascimento do protocolo (D4); no escopo `atendimento` ela apenas **semeia o popup**, porque o ciclo é resolvido logo em seguida e materializar ali seria imediatamente sobrescrito pelo formulário.

---

## 2. Como funciona hoje (mapa verificado)

| # | Fato | Onde |
|---|---|---|
| 1 | Dois escopos: `SCOPES = ("protocolo", "atendimento")`, ambos com extras | [logic.py:52-53](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L52-L53) |
| 2 | Tabelas de valor **separadas** por escopo: `protocolo_extras(protocolo_id)` × `campos_extras(atendimento_id)` | [logic.py:104-107](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L104-L107), migrations [003](../../whatsbot-pro-plugins/plugins/protocolos/src/migrations/003_campos_extras.sql)/[004](../../whatsbot-pro-plugins/plugins/protocolos/src/migrations/004_protocolo_extras.sql) |
| 3 | `upsert_extra(conn, scope, owner_id, d, value)` é **genérico**: escreve em qualquer um dos dois | [logic.py:504-515](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L504-L515) |
| 4 | A definição de um campo é um **literal fechado** de 12 chaves — chave nova só existe se for acrescentada ali | [logic.py:292-307](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L292-L307) (`_normalize_extra_def`) |
| 5 | Defs legadas recebem os campos que faltam por `setdefault` na leitura | [logic.py:211-223](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L211-L223) (`get_extra_defs`) |
| 6 | `GET /field-defs?scope=` e `PUT /field-defs` são o único caminho de leitura/escrita das definições | [routes.py:621-635](../../whatsbot-pro-plugins/plugins/protocolos/src/routes.py#L621-L635) |
| 7 | O popup de resolver é injetado pelo plugin em `filter.conversation.beforeResolve`, busca `scope=atendimento` e grava com `POST /atendimentos/{id}/resolve` | [extends.js:177](../../whatsbot-pro-plugins/plugins/protocolos/src/static/extends.js#L177), [:196](../../whatsbot-pro-plugins/plugins/protocolos/src/static/extends.js#L196), [:228](../../whatsbot-pro-plugins/plugins/protocolos/src/static/extends.js#L228) |
| 8 | ⚠️ O popup **nasce sempre em branco de propósito** (1.23.0): semear com o espelho fazia um atendimento herdar os valores do anterior, inclusive entre operadores | [extends.js:200-208](../../whatsbot-pro-plugins/plugins/protocolos/src/static/extends.js#L200-L208), [proto_fields.js:88-96](../../whatsbot-pro-plugins/plugins/protocolos/src/static/proto_fields.js#L88-L96) |
| 9 | `resolve_atendimento` já resolve/cria o protocolo aberto e tem o `at["id"]` | [logic.py:3004-3006](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L3004-L3006) |
| 10 | ⚠️ O espelho em `conversations.custom_attributes` é **exclusivo do escopo `atendimento`** (`if scope == "atendimento"`), e `sync_core_atendimento_defs` só lê `get_field_defs("atendimento")` | [logic.py:346-347](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L346-L347), [:679](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L679), [:723](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L723) → **é o que garante a D1 de graça** |
| 11 | `LabeledField`/`FieldInput` são **compartilhados** pelo popup e pelo modal do protocolo — um só ponto de render | [resolve_form.js:55](../../whatsbot-pro-plugins/plugins/protocolos/src/static/resolve_form.js#L55), [:111](../../whatsbot-pro-plugins/plugins/protocolos/src/static/resolve_form.js#L111); consumido em [protocolos_tab.js:1815](../../whatsbot-pro-plugins/plugins/protocolos/src/static/protocolos_tab.js#L1815) |
| 12 | ⚠️ **"Resultado" existe nos DOIS escopos**, com rótulos parecidos | [logic.py:150-154](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L150-L154) (protocolo) × [:166-168](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L166-L168) (atendimento) — por isso a D2 |
| 13 | `OptionListSelect` (core) **sempre** oferece "Limpar seleção"; single-select não tem opção vazia na lista, só o rodapé | [OptionListSelect.js:66](../web/static/js/components/OptionListSelect.js#L66), [:106-107](../web/static/js/components/OptionListSelect.js#L106-L107) |
| 14 | O protocolo nasce num único ponto, com `fields = '{}'` e nenhum extra | [logic.py:841-880](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L841-L880) (`ensure_protocolo_for_contact`) |
| 15 | São **3** os call sites que abrem o `ResolveForm` | [extends.js:212](../../whatsbot-pro-plugins/plugins/protocolos/src/static/extends.js#L212) (botão Resolver), [extends.js:56](../../whatsbot-pro-plugins/plugins/protocolos/src/static/extends.js#L56) ("fechar conversa e protocolo juntos"), [protocolos_tab.js:1148](../../whatsbot-pro-plugins/plugins/protocolos/src/static/protocolos_tab.js#L1148) (resolver forçado do Kanban) |
| 16 | O modal do protocolo salva em bloco (`PUT /protocolos/{id}/fields`), que **mescla** com o salvo | [protocolos_tab.js:1691](../../whatsbot-pro-plugins/plugins/protocolos/src/static/protocolos_tab.js#L1691), [logic.py:1016](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L1016) |
| 17 | Arrastar card no Kanban grava campo de protocolo por `POST /protocolos/{id}/set-field` | [protocolos_tab.js:411](../../whatsbot-pro-plugins/plugins/protocolos/src/static/protocolos_tab.js#L411), [routes.py:453](../../whatsbot-pro-plugins/plugins/protocolos/src/routes.py#L453) |
| 18 | `_broadcast_changed` já é chamado no fim do resolve → o Kanban se atualiza sozinho | [logic.py:3045](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L3045) |
| 19 | O precedente de chave aditiva + poda no save é o **plano 102** (`description` / `option_descriptions`) | [logic.py:245-270](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L245-L270) |
| 20 | ⚠️ A fonte de desenvolvimento é `../whatsbot-pro-plugins/plugins/protocolos/src/` (**1.26.1** desde o port de 2026-08-05). O espelho em `assets/plugin_examples/protocolos/` está em **1.25.0** e **não** é fonte — **mas é de linhagem MISTA**: apesar do número menor e do `logic.py` mais antigo, ele é o único lugar em git que tinha o `retornos_fields.py` (byte-idêntico ao de produção) e o `filters.py` com os 2 seams. Foi de lá que a 1.26.1 saiu. **Ao comparar cópias, compare CONTEÚDO — nunca só o número de versão** | `CLAUDE.md` → "Onde vive o código de um plugin"; `diff -q assets/plugin_examples/protocolos/retornos_fields.py <zip de produção>` → idênticos |
| 21 | ⚠️ **A coluna "Sem valor" do Kanban é ALVO DE ARRASTO e LIMPA o campo** — `onDrop` manda `value: null` e o texto de confirmação é *"Limpar «Temperatura» do protocolo de X?"*. É uma **terceira superfície** de escrita, além do popup e do modal | [protocolos_tab.js:405-413](../../whatsbot-pro-plugins/plugins/protocolos/src/static/protocolos_tab.js#L405-L413), `COL_NONE = "__none__"` em [grouping.py:37](../../whatsbot-pro-plugins/plugins/protocolos/src/grouping.py#L37), [logic.py:2993-2997](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L2993) (`value` None/"" limpa) |
| 22 | ⚠️ **O nascimento do protocolo NÃO invalida o cache do Kanban.** O índice é cache em memória (TTL 30s) invalidado por um contador de geração que só `_broadcast_changed` bumpa — e **nenhuma** função do caminho de inbound (`on_inbound` → `ensure_protocolo_for_contact` → `ensure_open_cycle` → `_sync_provisional_from_conv`) o chama. Pré-existente; afeta o CRITÉRIO DE ACEITE da F3 | [kanban_index.py:1-45](../../whatsbot-pro-plugins/plugins/protocolos/src/kanban_index.py#L1-L45) (`_CACHE_TTL = 30.0`, `GENERATION_KEY`), [logic.py:4826](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L4826) (`_broadcast_changed` → `bump_generation`); os 17 call sites dele **não** incluem o caminho de nascimento |
| 23 | ⚠️ **`retornos_fields.py` é um consumidor NOVO das definições E dos valores** dos dois escopos: `_campos_configuraveis` monta o catálogo de condições lendo `get_field_defs(scope)`, e `_valores_configuraveis` resolve o valor lendo `entidade["fields"]` — o MESMO dicionário de extras que a F3 passa a preencher no nascimento | [retornos_fields.py:72-91](../../whatsbot-pro-plugins/plugins/protocolos/src/retornos_fields.py#L72-L91) e [:171-182](../../whatsbot-pro-plugins/plugins/protocolos/src/retornos_fields.py#L171-L182); [logic.py:592](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L592) (`d["fields"] = extras`) |
| 24 | O plugin CONSUMIDOR (`retornos`) **não está instalado neste dev** — o que está instalado é o `retorno_automatico` 1.0.1, outro id. Logo, os efeitos dos itens #21/#23 **não aparecem testando local** | `ls storages/plugins/` → `retorno_automatico`; a fonte do `retornos` 1.16.0 está em `assets/plugin_examples/retornos/` |

---

## 3. Inventário da mudança

| # | Item | Onde | O que falta | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|---|
| 1 | Chave `show_on_resolve` na def | [logic.py:292-307](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L292-L307) + [:211-223](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L211-L223) | a def é literal fechado (§2 #4) | acrescentar a chave; **forçar `False` fora do escopo `protocolo`** (D8) — `_normalize_extra_def` não conhece o escopo, quem sabe é `set_field_defs` | baixo | **S** |
| 2 | Chave `default_value` na def | idem | idem | acrescentar + validar com `_coerce_extra` ([logic.py:376](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L376)) e **podar** default órfão (opção renomeada) no mesmo PUT, igual ao plano 102 | baixo | **M** |
| 3 | Controles na tela Configurar | [config.js:308-409](../../whatsbot-pro-plugins/plugins/protocolos/src/static/config.js#L308-L409) | não existem | checkbox "Mostrar ao resolver atendimento" (só aba Protocolo) + widget "Valor padrão" por tipo; semear no `addField` ([config.js:175](../../whatsbot-pro-plugins/plugins/protocolos/src/static/config.js#L175)) e no `save` ([:187](../../whatsbot-pro-plugins/plugins/protocolos/src/static/config.js#L187)) | baixo | **M** |
| 4 | Protocolo nasce com os padrões | [logic.py:875-880](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L875-L880) | nasce sem extra nenhum (§2 #14) | só quando `created` é True, gravar `default_value` de cada def do escopo `protocolo` com `upsert_extra` | médio | **M** |
| 5 | Write path do atalho | [logic.py:2985](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L2985) + [routes.py:601](../../whatsbot-pro-plugins/plugins/protocolos/src/routes.py#L601) | `/resolve` só grava extras de `atendimento` | `proto_fields` novo e **opcional** no corpo; normalizar no escopo `protocolo`, filtrar por `show_on_resolve`, `upsert_extra` na MESMA transação | médio | **M** |
| 6 | Read path do atalho | [extends.js:194-198](../../whatsbot-pro-plugins/plugins/protocolos/src/static/extends.js#L194-L198) | o popup só busca `scope=atendimento` | reusar `GET /field-defs?scope=protocolo` + `GET /contacts/{cid}/protocolo` ([routes.py:241](../../whatsbot-pro-plugins/plugins/protocolos/src/routes.py#L241)) — ambos já existem e já são gateados por `view`. Ver **P2** | baixo | **S** |
| 7 | Seção "Protocolo" no popup | [resolve_form.js:139-189](../../whatsbot-pro-plugins/plugins/protocolos/src/static/resolve_form.js#L139-L189) | o popup renderiza uma lista só | props novas `protoDefs`/`protoValues`; `onOk` passa a devolver `{ fields, protoFields, goTo }` | baixo | **M** |
| 8 | "Sem opção vazia" (D6) | [resolve_form.js:64-94](../../whatsbot-pro-plugins/plugins/protocolos/src/static/resolve_form.js#L64-L94) | "Limpar seleção" esvazia (§2 #13) | interceptar o `onChange` **no plugin**: valor vazio + def com padrão ⇒ volta ao padrão. Um só ponto cobre popup e modal (§2 #11) | baixo | **S** |
| 9 | Helpers puros + testes | [proto_fields.js](../../whatsbot-pro-plugins/plugins/protocolos/src/static/proto_fields.js) + `tests/` | — | `defaultValueFor(def)` e `coerceNonEmpty(def, v)` no módulo puro; `node --test` + pytest do runner externo | baixo | **M** |

### Falsos positivos descartados

| Suspeita | Por que NÃO é o caminho |
|---|---|
| "Basta um `clearable={false}` no `OptionListSelect` do core" | Vira mudança de core para um consumidor só — reprovado pela regra dos 3 critérios (`CLAUDE.md` → "O que fica no core e o que vai pro plugin"). O plugin resolve sozinho no `onChange` (item 8), e ainda funciona num core antigo |
| "É só criar o campo Temperatura no escopo `atendimento`" | Aí o valor passa a ser **por ciclo** (um por atendimento resolvido), some do card/Kanban do protocolo e **vira atributo de conversa no core** pelo espelho (§2 #10) — o oposto da D1 |
| "Precisa de migration para a chave nova" | Não. As definições vivem em `config` (`plugin.protocolos.field_defs_<scope>`, [logic.py:173-174](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L173-L174)) e os valores já cabem no JSON auto-descritivo do extra ([logic.py:493](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L493)) |
| "Precisa de endpoint novo para gravar o campo do protocolo" | `POST /protocolos/{id}/set-field` já existe ([routes.py:453](../../whatsbot-pro-plugins/plugins/protocolos/src/routes.py#L453)), mas usá-lo custaria uma 2ª chamada **depois** do resolve, sem transação comum e com o protocolo podendo nascer no meio. O `proto_fields` no `/resolve` é atômico |
| "O `_check_before_status` precisa exigir o campo do protocolo" | D7 diz o contrário — e exigir ali travaria o fechamento da conversa por um campo que nem é do atendimento |
| "Materializar o padrão também na criação do ciclo (`ensure_open_cycle`)" | O popup do `atendimento` **nasce em branco** (§2 #8) e sobrescreveria o valor materializado no ato do resolve. No escopo `atendimento` o padrão é **semente de formulário** — ver P1 |
| "Semear o modal do protocolo com o padrão quando o campo está vazio" | Isso preencheria os protocolos ANTIGOS (viola a D5). O padrão entra no **nascimento**, nunca na leitura de uma entidade que já existe |
| "Atualizar `assets/plugin_examples/protocolos/`" | Não é fonte e está em 1.25.0 (§2 #20). O `protocolos` **não** é bundled (`BUNDLED_AUTO_INSTALL = ("gowa",)`). ⚠️ **Mas CONFERIR se a fonte não perdeu arquivo que só existe lá: sim, antes de todo build** — foi exatamente assim que o `retornos_fields.py` sumiu da 1.26.0 |
| "O arrastar do Kanban não é afetado (§2 #17)" | 🔴 **ERRADO — revisado em 2026-08-05.** A coluna "Sem valor" é alvo de arrasto e **limpa** o campo (§2 #21). Com a D6 em vigor, ela vira a ÚNICA forma de esvaziar um campo com padrão — o que é uma incoerência a decidir de propósito, não a ignorar. Ver **P6** |

---

## 4. Fases / Roadmap

```
WAVE 0   F0 (caracterização)                                  🔴 sozinha — trava o que existe hoje
             │
WAVE 1   F1 (backend: 2 chaves novas na def)                  🔴 sozinha — habilitador de tudo
             │
             ├── [bloqueia: F2, F3, F4, F6]
WAVE 2   F2 (tela Configurar) · F3 (nascimento) · F4 (write)  🟢 paralelas entre si
             │                                    │
             │                                    └── [bloqueia: F5]
WAVE 3   F5 (popup: seção + seed + envio)                     🔴 sozinha
             │
WAVE 4   F6 (sem opção vazia)                                 🔴 sozinha [mesmo arquivo da F5]
             │
WAVE 5   F7 (testes, doc, versão, zip)                        🔴 sozinha
```

| Wave | Fase | Workstream | Paraleliza? | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | Testes de caracterização | 🔴 | baixo | 3 testes novos verdes travando o comportamento atual |
| 1 | **F1** | Backend — `show_on_resolve` + `default_value` na definição | 🔴 | baixo | round-trip do `PUT`/`GET /field-defs` preserva as 2 chaves |
| 2 | **F2** | Frontend — controles na tela Configurar | 🟢 [depende de: F1] | baixo | dá para marcar o atalho e escolher "Frio" como padrão, e recarregar mantém |
| 2 | **F3** | Backend — protocolo nasce com os padrões | 🟢 [depende de: F1] | médio | contato novo manda mensagem ⇒ card já aparece na coluna "Frio" |
| 2 | **F4** | Backend — `proto_fields` no `POST /resolve` | 🟢 [depende de: F1] | médio | `curl` com `proto_fields` grava no protocolo e **não** cria atributo de conversa |
| 3 | **F5** | Frontend — seção "Protocolo" no popup | 🔴 [depende de: F4] | médio | resolver um atendimento com "Morno" move o card no Kanban |
| 4 | **F6** | Frontend — campo com padrão nunca fica vazio | 🔴 [depende de: F1; mesmo arquivo da F5] | baixo | "Limpar seleção" volta para "Frio" no popup **e** no modal do protocolo |
| 5 | **F7** | Testes, doc, versão 1.27.0, zip | 🔴 [depende de: todas] | baixo | runner do plugin verde + zip reconstruído + instalado local |

---

### F0 — Caracterização antes de mexer (🔴 sozinha)

**Objetivo:** travar por teste o que **não pode** mudar — é o que vai acusar se a D1 ou a D5 forem quebradas por acidente.

**Itens:**
1. `[sequencial]` `tests/python/`: o conjunto de chaves de uma def é um literal fechado hoje (§2 #4). O teste equivalente já existe em [test_field_descriptions.py:42-58](../../whatsbot-pro-plugins/plugins/protocolos/tests/python/test_field_descriptions.py#L42-L58) — **estender** a asserção na F1, não duplicar.
2. `[paralelo]` Novo teste: salvar um campo no escopo **`protocolo`** NÃO cria definição de atributo de conversa no core (`custom_attribute_repo.list_definitions(applies_to="conversation")` fica igual) — é a D1 travada. Contrasta com o escopo `atendimento`, que cria (§2 #10).
3. `[paralelo]` Novo teste: `ensure_protocolo_for_contact` hoje cria protocolo **sem nenhum extra** (`fields == {}`) — é a linha de base da F3.
4. `[paralelo]` `tests/js/proto_fields.test.js`: `seedResolveValues` hoje devolve `''`/`[]`/`false` para tudo que não é `atendente` — linha de base da F1/F5.

**Pronto quando:** `python3 scripts/test_plugins.py protocolos` verde com os testes novos, **antes** de qualquer mudança de comportamento.

#### Status de execução — Fase 0
**Estado:** ✅ Concluída (2026-08-05)
- **O que foi feito:** criado `tests/python/test_field_default_and_shortcut.py` com 3 testes de caracterização, rodados **antes** de qualquer mudança de comportamento: (1) campo do escopo `protocolo` NÃO cria definição de atributo de conversa no core — com o contraste do escopo `atendimento`, que cria; (2) `ensure_protocolo_for_contact` cria protocolo com `fields == {}`; (3) `/resolve` de hoje não toca no protocolo.
- **Como foi feito / decisões:** o item 1 do plano (asserção do literal de chaves) não foi duplicado — foi ampliado na F1, como o plano manda. O teste da D1 ganhou o **contraste** com o escopo `atendimento`: sem ele, um espelho que parasse de funcionar por outro motivo passaria por "D1 respeitada". O item 4 (caracterização JS do `seedResolveValues`) já estava coberto pelo teste existente *"sem initialValues todo campo nasce vazio"*, que foi mantido intacto e agora prova a não-regressão.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `pytest test_field_default_and_shortcut.py` ⇒ **3 passed** contra a fonte 1.26.x não modificada.

---

### F1 — Duas chaves novas na definição do campo (🔴 sozinha — habilitador)

**Objetivo:** a definição passar a carregar `show_on_resolve` e `default_value`, com validação e poda, sem migration e sem quebrar def legada.

**Itens:**
1. `[sequencial]` [logic.py:292-307](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L292-L307) (`_normalize_extra_def`): acrescentar as duas chaves ao literal.
   - `show_on_resolve: bool` — sempre `False` para o tipo `atendente` (o fixo já aparece no popup por outro caminho).
   - `default_value` — validado por `_coerce_extra(d, raw)` ([logic.py:376](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L376)), que já sabe coagir por tipo e recusar opção inválida. Erro ⇒ `ValueError` com o rótulo, igual às outras validações do save.
   - **Poda**: default que não está mais em `options` (opção renomeada/apagada no MESMO PUT) é descartado em silêncio — mesma política de `_option_descriptions` ([logic.py:245-270](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L245-L270)). A poda roda **depois** da normalização de `options`.
   - Tipo sem padrão possível: `atendente` (o valor é um uid resolvido de usuário) ⇒ sempre vazio.
2. `[sequencial]` [logic.py:310-348](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L310-L348) (`set_field_defs`): é aqui que o **escopo** é conhecido — zerar `show_on_resolve` quando `scope != "protocolo"` (D8). Não confiar no cliente.
3. `[sequencial]` [logic.py:211-223](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L211-L223) (`get_extra_defs`): `setdefault` das duas chaves para def legada — `False` e o vazio do tipo (`''` / `[]` / `False`).
4. `[paralelo]` Ampliar a asserção de chaves de [test_field_descriptions.py:56-58](../../whatsbot-pro-plugins/plugins/protocolos/tests/python/test_field_descriptions.py#L56-L58) para 14 chaves (senão ela quebra vermelha sem informar nada).
5. `[paralelo]` Nada muda em `normalize_values` ([logic.py:432](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L432)) nem em `_missing_required` ([logic.py:466](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L466)) — o padrão **não** é aplicado na leitura de valor (D5).

**Pronto quando:** `PUT /field-defs` com `{show_on_resolve: true, default_value: "Frio"}` volta no `GET` idêntico; `default_value: "Gelado"` (fora das opções) é recusado com mensagem citando o rótulo; def antiga (sem as chaves) continua carregando.

#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-08-05)
- **O que foi feito:** em `logic.py` — `_normalize_extra_def(d, scope=None)` passou a devolver 14 chaves (as 12 + `show_on_resolve` + `default_value`); `set_field_defs` passa o escopo; `get_extra_defs` faz o `setdefault` das duas e **reafirma na leitura** que `show_on_resolve` só vale no escopo `protocolo`. Helpers novos: `_NO_DEFAULT_TYPES`, `_empty_default`, `_is_blank_value`, `_has_default`, `_normalize_default`, `resolve_shortcut_defs()`, `default_extra_values(scope)`. Asserção de chaves de `test_field_descriptions.py` ampliada para 14.
- **Como foi feito / decisões:** **duas políticas distintas para duas falhas distintas** — o "Pronto quando" do plano pedia poda *e* recusa para a mesma coisa, o que é contraditório. Resolvido separando: **pertencer às opções** é PODADO em silêncio (renomear a opção padrão é gesto legítimo e frequente, e o widget escolhe da lista — recusar tornaria o rename impossível sem antes limpar o padrão); **ser inválido por TIPO** (número, data, regex) é RECUSADO com o rótulo (nenhum rename produz isso, o input é livre nesses tipos, e podar engoliria erro real). A poda roda **antes** da coerção — sem isso, `_coerce_extra` recusaria opção inválida em `checkboxes` e o rename quebraria o salvamento inteiro. `show_on_resolve` é decidido no `set_field_defs` (que conhece o escopo) **e** reafirmado no `get_extra_defs`, para def escrita à mão no config não furar a D8.
- **Problemas / pendências:** adotado o **P7(a)** (revisão do plano de 2026-08-05): `checkbox` entrou em `_NO_DEFAULT_TYPES` junto do `atendente`. Enforcement no **normalizador e na leitura**, não só na tela — esconder o widget não impediria um padrão vindo por API de ser materializado pela F3.
- **Verificação:** `pytest test_field_default_and_shortcut.py` (round-trip, poda em single e multi, recusa por tipo, def legada por tipo) + `test_field_descriptions.py` 17 passed.

---

### F2 — Controles na tela Configurar (🟢) [depende de: F1]

**Objetivo:** o operador conseguir marcar o atalho e escolher o valor padrão sem tocar em JSON.

**Itens:**
1. `[sequencial]` [config.js:308-409](../../whatsbot-pro-plugins/plugins/protocolos/src/static/config.js#L308-L409): na linha do campo, ao lado de "Obrigatório" ([:329-332](../../whatsbot-pro-plugins/plugins/protocolos/src/static/config.js#L329-L332)), um checkbox **"Mostrar ao resolver atendimento"** — renderizado **apenas** quando `tab === 'protocolo'` e o campo não é fixo. Com uma linha de ajuda curta: *"o valor continua sendo do protocolo; no popup é só um atalho"*.
2. `[sequencial]` Bloco **"Valor padrão"**, por tipo:
   | Tipo | Widget |
   |---|---|
   | `select` / `radio` | seleção única entre `d.options` (+ "sem padrão") |
   | `checkboxes` | seleção múltipla entre `d.options` (+ "sem padrão") |
   | `checkbox` | um checkbox ("marcado por padrão") — ⚠️ **ver P7 antes de implementar** |
   | `text` / `textarea` / `number` / `date` | input do mesmo tipo |
   | `atendente` | **não exibir** |
   O widget deve reagir à edição de `options` (opção apagada ⇒ o padrão some da seleção — a poda definitiva é do servidor, F1 item 1; espelha o comportamento de `optionDescriptionRows`, [proto_fields.js:145](../../whatsbot-pro-plugins/plugins/protocolos/src/static/proto_fields.js#L145)).
3. `[paralelo]` [config.js:175](../../whatsbot-pro-plugins/plugins/protocolos/src/static/config.js#L175) (`addField`): semear as duas chaves no campo novo.
4. `[paralelo]` [config.js:187-204](../../whatsbot-pro-plugins/plugins/protocolos/src/static/config.js#L187-L204) (`save`): as chaves seguem no payload (o `...d` já as leva; conferir que a normalização de `options` não as derruba).
5. `[paralelo]` Texto de ajuda do topo ([config.js:296-305](../../whatsbot-pro-plugins/plugins/protocolos/src/static/config.js#L296-L305)): uma frase sobre o atalho e uma sobre o padrão.
6. `[paralelo]` Cores só com `wa-*`/`.wa-field` e conferir no **modo escuro** (regra do `CLAUDE.md`).

**Pronto quando:** criar "Temperatura" (lista Frio/Morno/Quente), marcar o atalho, escolher "Frio" como padrão, salvar, **recarregar** e ver tudo preservado. Na aba "Resolver atendimento" o checkbox de atalho **não aparece**.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-08-05)
- **O que foi feito:** em `static/config.js` — componente `DefaultValueField` (widget por tipo), checkbox **"Mostrar ao resolver atendimento"** ao lado de "Obrigatório" e **só na aba Protocolo**, `addField` semeando as duas chaves, e um parágrafo de ajuda no topo do field-builder (a frase sobre o atalho só aparece na aba Protocolo).
- **Como foi feito / decisões:** widgets — `select`/`radio` = `OptionListSelect` simples; `checkboxes`/`select multiple` = o mesmo em modo múltiplo; `text`/`textarea`/`number`/`date` = input nativo do tipo; `checkbox` e `atendente` **não renderizam nada** (P7). As opções do seletor saem de `splitOptionList(d.options)` **ao vivo**, e o valor exibido é podado pelas opções vivas — apagar uma opção some com ela da escolha na hora (a poda definitiva é do servidor). O `save` já leva as chaves pelo `...d`.
- **Problemas / pendências:** `OptionListSelect` (core) **não tem prop `disabled`** — sem tratamento, um operador sem permissão de editar conseguiria mexer no seletor (o PUT 403aria depois, mas a tela já teria mentido). Envolvido num `div` com `pointer-events-none opacity-60` quando bloqueado, em vez de mudar o componente do core.
- **Verificação:** só classes `wa-*`/`.wa-field` (+ `OptionListSelect`, que já é dark-safe); `node --input-type=module --check` no módulo. Conferência visual nos dois temas fica junto do teste de interface da F7 item 5.

---

### F3 — Protocolo nasce com os valores padrão (🟢) [depende de: F1]

**Objetivo:** cumprir a D4 — o card já nasce na coluna certa do Kanban, sem backfill.

**Itens:**
1. `[sequencial]` [logic.py:875-880](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L875-L880): **somente quando `created` é `True`** e o `at` foi re-selecionado, gravar com `upsert_extra(conn, "protocolo", at["id"], d, default)` cada def do escopo `protocolo` que tenha padrão não-vazio. Quem **perde a corrida** (`IntegrityError`, [:873](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L873)) não escreve nada — o vencedor já escreveu.
2. `[sequencial]` Pular `type == "atendente"` (não é extra, vira assignee nativo — mesma exclusão de [logic.py:1028](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L1028)).
3. `[paralelo]` **Best-effort**: falha ao gravar padrão nunca pode impedir a abertura do protocolo (o chamador é o handler de `message.saved`, [logic.py:3076](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L3076)) — `try/except` com `logger.debug`, no padrão do arquivo.
4. `[paralelo]` **Não** tocar em `ensure_open_cycle` ([logic.py:2964](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L2964)) — ver P1 e o falso positivo correspondente.
5. `[paralelo]` Custo: só roda na criação real, não a cada mensagem.
6. `[paralelo]` ⚠️ **NÃO** acrescentar `_broadcast_changed` aqui só para o card aparecer mais rápido (§2 #22). Seria mudança de comportamento fora do escopo deste plano, no caminho mais quente do plugin (todo inbound passa por `ensure_protocolo_for_contact`) e com efeito de invalidação global de cache. O atraso é **pré-existente** — ver o critério de aceite abaixo.

**Pronto quando:** com "Temperatura" padrão "Frio", um contato **novo** manda mensagem e, **depois que o índice do Kanban renovar**, o card aparece na coluna **Frio** (não em "Sem valor"); os protocolos que já existiam continuam em "Sem valor" (D5).

⚠️ **Como NÃO se enganar neste teste** (§2 #22): o caminho de nascimento não bumpa a geração do Kanban, então o card novo demora **até 30 s** (o `_CACHE_TTL`) para entrar no índice — isso vale hoje, antes deste plano. Verificar o card imediatamente após a mensagem produz **falso negativo**. Formas confiáveis de conferir: (a) esperar ~30 s e recarregar; (b) forçar um bump com qualquer escrita (arrastar outro card, salvar um campo); ou — melhor — (c) verificar direto no banco que a linha nasceu:
`SELECT payload FROM plugin_protocolos_protocolo_extras WHERE protocolo_id = <id>;`

⚠️ **Consequência fora do plugin** (§2 #23): a partir desta fase, todo protocolo novo carrega o valor padrão em `fields` — e é dali que o construtor de regras do `retornos` lê. Uma régua salva com *"`protocolos.campo.temperatura` está vazio"* **deixa de casar** para protocolos novos, e *"= Frio"* passa a casar em todos eles, inclusive nos que ninguém tocou. Ver §5 e o checklist.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída (2026-08-05)
- **O que foi feito:** `ensure_protocolo_for_contact` ganhou, **dentro do ramo `created`**, a chamada a `_seed_default_extras(at["id"])` (novo), que grava com `upsert_extra` cada def do escopo `protocolo` com padrão não-vazio.
- **Como foi feito / decisões:** quem **perde a corrida** do índice parcial (`IntegrityError`) não entra no ramo `created` e portanto não escreve — o vencedor já semeou. Quando algo foi semeado, o protocolo é **re-selecionado** antes de voltar: `_proto_dict` monta `fields` a partir dos extras, e sem o re-select o chamador (ex.: `POST /contacts/{id}/protocolo/ensure`) receberia um protocolo que "nasceu vazio". A nota de abertura passou para dentro do mesmo ramo, sem mudança de comportamento. **Best-effort** com `try/except` + `logger.debug`: o chamador mais quente é o handler de `message.saved`, e um padrão que não grava é um card na coluna errada — um protocolo que não abre é atendimento perdido.
- **Problemas / pendências:** ⚠️ **§2 #22 (pré-existente) afeta o critério de aceite**: o nascimento do protocolo não bumpa a geração do cache do Kanban (TTL 30s), então o card pode demorar até ~30s para aparecer — **na coluna certa**. Não foi "consertado" aqui de propósito: bumpar a geração no caminho de inbound é mudança de comportamento no caminho quente, fora do escopo deste plano.
- **Verificação:** `pytest` — nasce com o padrão (select, multi, date), protocolo que já existe **não** é semeado, protocolo **fechado** não é tocado quando um novo nasce para o mesmo contato, e `checkbox`/`atendente` nunca materializam (P7).

---

### F4 — `proto_fields` no `POST /atendimentos/{id}/resolve` (🟢) [depende de: F1]

**Objetivo:** o atalho gravar no protocolo, na mesma transação do resolve, sem espelho no core.

**Itens:**
1. `[sequencial]` [logic.py:2985](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L2985) (`resolve_atendimento`): parâmetro novo `proto_values: dict | None = None` (default `None` ⇒ comportamento byte-idêntico ao de hoje).
2. `[sequencial]` Depois do `ensure_protocolo_for_contact` ([:3004](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L3004)) o `at["id"]` já existe. Normalizar com `normalize_values("protocolo", proto_values)` e **filtrar**: só chaves cuja def tem `show_on_resolve` **e** `type != "atendente"`. O filtro é defesa — o popup não pode virar um caminho para escrever qualquer campo do protocolo.
   ⚠️ `normalize_values` **exige os obrigatórios do escopo** ([logic.py:446-450](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L446-L450)): um campo do protocolo obrigatório e **não** exibido no popup faria o resolve falhar. Usar `_coerce_extra` por chave (ou uma variante `partial=True`) em vez do `normalize_values` inteiro — **é o ponto mais fácil de errar desta fase** (D7).
3. `[sequencial]` Gravar dentro do `with make_plugin_db() as conn:` que já existe ([logic.py:3024-3037](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L3024-L3037)), com `upsert_extra(conn, "protocolo", at["id"], d, v)`.
4. `[sequencial]` **Não** chamar `mirror_atendimento_to_core` com esses valores ([logic.py:3044](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L3044)) — ele recebe só o `clean` do escopo `atendimento` (D1).
5. `[sequencial]` [routes.py:601-609](../../whatsbot-pro-plugins/plugins/protocolos/src/routes.py#L601-L609): repassar `(body or {}).get("proto_fields")`. Corpo sem a chave = comportamento atual.
6. `[paralelo]` Gravar **apenas as chaves que mudaram** em relação ao valor que o popup recebeu (o cliente manda o que exibiu). Protege dois casos: (a) não criar linha de extra com `''` num protocolo legado (D5); (b) não reverter um valor que outra pessoa mudou no Kanban enquanto o popup estava aberto (§5).
7. `[paralelo]` `_broadcast_changed` já roda no fim ([:3045](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L3045)) — o Kanban se atualiza sem código novo.

**Pronto quando:** `curl -X POST /api/plugins/protocolos/atendimentos/<id>/resolve -d '{"fields":{},"proto_fields":{"temperatura":"Quente"}}'` grava em `plugin_protocolos_protocolo_extras`, **não** cria linha em `custom_attribute_definitions` (`applies_to=conversation`) e **não** escreve em `conversations.custom_attributes`; mandar uma chave sem `show_on_resolve` é ignorada em silêncio.

#### Status de execução — Fase 4
**Estado:** ✅ Concluída (2026-08-05)
- **O que foi feito:** `resolve_atendimento(..., proto_values=None)`; helpers `_coerce_shortcut_values` e `_prunable_shortcut`; a escrita entrou **dentro do `with make_plugin_db() as conn:` que já existia**, com `upsert_extra(conn, "protocolo", at["id"], d, v)`. `routes.py` repassa `(body or {}).get("proto_fields")`. `mirror_atendimento_to_core` **não** foi tocado — segue recebendo só o `clean` do escopo `atendimento` (D1).
- **Como foi feito / decisões:** o **required parcial** (risco nº 1) foi resolvido não usando `normalize_values` — a coerção é **chave a chave** com `_coerce_extra`, sem gate de obrigatório, e só passa chave cuja def tem `show_on_resolve` e não é `atendente` (o popup não pode virar caminho de escrita para qualquer campo do protocolo). A coerção roda **antes de qualquer escrita**: valor malformado devolve erro com o ciclo intacto, em vez de resolver pela metade. Valor inválido **retorna erro** em vez de ser descartado em silêncio — mesmo tratamento que os campos do escopo `atendimento` já têm; descartar faria o atendente achar que salvou.
- **Problemas / pendências:** (1) o item 6 do plano ("gravar só o que mudou") é do **cliente**, não do servidor — o servidor conhece o valor atual, não o que o popup exibiu, e um diff contra o atual reverteria a mudança de quem arrastou o card no Kanban durante o popup. O servidor tem só a rede de segurança de D5: `_prunable_shortcut` descarta valor **vazio** em rótulo que ainda **não tem linha** no protocolo (limpar de propósito continua funcionando quando a linha existe). (2) **Permissão**: o atalho fica sob `resolve`, não sob `edit` — decisão consciente, documentada na rota: o atalho existe para o atendente que resolve e pode não gerenciar protocolos; a superfície é estreita e opt-in (só campos MARCADOS).
- **Verificação:** `pytest` — grava no protocolo e **não** cria atributo/valor de conversa no core; chave não marcada/inexistente ignorada; obrigatório do protocolo fora do atalho **não** quebra o resolve; valor inválido não resolve nada; vazio não cria linha em protocolo sem valor, mas limpa onde a linha existe; corpo sem a chave = comportamento anterior; e o contrato HTTP real via `POST /api/plugins/protocolos/atendimentos/{id}/resolve`.

---

### F5 — Seção "Protocolo" no popup de resolver (🔴 sozinha) [depende de: F4]

**Objetivo:** o atendente ver e ajustar a temperatura sem sair da conversa (D2, D3).

**Itens:**
1. `[sequencial]` [resolve_form.js:139-189](../../whatsbot-pro-plugins/plugins/protocolos/src/static/resolve_form.js#L139-L189) (`ResolveForm`): props novas `protoDefs = []` e `protoValues = {}`; estado separado para elas (não misturar com `vals`, senão uma chave homônima nos dois escopos se sobrescreve — §2 #12). `onOk` passa a devolver `{ fields, protoFields, goTo }`.
2. `[sequencial]` Render: divisória + título **"Protocolo"** + linha de ajuda (*"vale para o protocolo inteiro, não só para este atendimento"*), abaixo dos campos do atendimento e acima dos botões. Sem `protoDefs`, o popup fica **byte-idêntico** ao de hoje.
3. `[sequencial]` [extends.js:194-198](../../whatsbot-pro-plugins/plugins/protocolos/src/static/extends.js#L194-L198): buscar em paralelo `GET /field-defs?scope=protocolo` (filtrando `show_on_resolve && !readonly && type !== 'atendente'`) e, quando houver algum, `GET /contacts/{contact_id}/protocolo` para os valores atuais. O `contactIdOf(atend)` já existe ([extends.js:78](../../whatsbot-pro-plugins/plugins/protocolos/src/static/extends.js#L78)). Falha em qualquer um ⇒ seguir **sem** a seção (nunca travar o resolver).
4. `[sequencial]` Semeadura (D3): valor salvo do protocolo. Protocolo **inexistente** (ainda não criado) ⇒ semear com o `default_value` — é o mesmo valor com que ele vai nascer na F3. Protocolo **existente** com valor vazio ⇒ fica vazio (D5).
5. `[sequencial]` [extends.js:228](../../whatsbot-pro-plugins/plugins/protocolos/src/static/extends.js#L228): mandar `proto_fields: result.protoFields` no POST.
6. `[paralelo]` Mesmos ajustes nos outros 2 call sites (§2 #15): `resolveAndCloseAll` ([extends.js:49-75](../../whatsbot-pro-plugins/plugins/protocolos/src/static/extends.js#L49-L75)) e `forceResolveAndClose` ([protocolos_tab.js:1141-1156](../../whatsbot-pro-plugins/plugins/protocolos/src/static/protocolos_tab.js#L1141-L1156)) — senão o atalho some dependendo de por onde o atendente fecha.
7. `[paralelo]` A obrigatoriedade do popup (`missing`, [resolve_form.js:143](../../whatsbot-pro-plugins/plugins/protocolos/src/static/resolve_form.js#L143)) **não** passa a considerar `protoDefs` (D7).
8. `[paralelo]` Modo escuro na seção nova.

**Pronto quando:** resolver um atendimento pelo botão do chat mostra "Temperatura" na seção Protocolo com o valor atual; trocar para "Quente" e resolver move o card no Kanban **sem** reload; a aba "Informações do atendimento" **não** ganha um atributo "Temperatura".

#### Status de execução — Fase 5
**Estado:** ✅ Concluída (2026-08-05)
- **O que foi feito:** `ResolveForm` ganhou `protoDefs`/`protoValues`, estado **separado** do `vals`, a seção "Protocolo" (divisória + título + linha de ajuda) e `onOk` devolvendo `{ fields, protoFields, goTo }`. Helpers puros novos em `proto_fields.js`: `seedProtoShortcut`, `sameFieldValue`, `changedValues`. Os **3** call sites passaram a alimentar e enviar: `beforeResolve` e `resolveAndCloseAll` (`extends.js`, via o novo `protoShortcut()`) e `forceResolveAndClose` (`protocolos_tab.js`).
- **Como foi feito / decisões:** **P2 = (a)**, reusando `GET /field-defs?scope=protocolo` + `GET /contacts/{cid}/protocolo` (ambas já gateadas por `view`); zero superfície nova. No 3º call site **nenhuma requisição a mais**: as defs já estão em `cols` (do `loadMeta`) e o protocolo veio do `GET /protocolos/{atid}` que a função já fazia. Falha em qualquer fetch ⇒ segue **sem** a seção (um atalho de digitação não pode travar o fechamento). Estado separado do `vals` porque **"Resultado" existe nos dois escopos** — um mapa só faria um sobrescrever o outro. `protoSeed` é congelado no mount: é a referência do diff de `changedValues`.
- **Problemas / pendências:** o `required` de um campo do protocolo **não** é renderizado no popup (`shortcutDef` zera antes do `LabeledField`): o asterisco marca o que ESTE formulário exige, e aqui nada é exigido (D7) — asterisco que não trava o botão ensina o atendente a ignorar asterisco. A exigência continua aparecendo, e valendo, no modal do protocolo. **P5 confirmado como esperado**: no caminho `outcome === 'resolved'` (fusão no protocolo anterior) o popup não abre e o atalho não aparece — o protocolo anterior já está finalizado (D5).
- **Verificação:** `node --test` (seed com/sem protocolo, diff); `node --input-type=module --check` nos 3 módulos. Aceite de interface (mover o card no Kanban ao resolver) junto da F7 item 5.

---

### F6 — Campo com padrão nunca fica vazio (🔴 sozinha) [depende de: F1]

**Objetivo:** cumprir a D6 nos **dois** formulários, sem tocar no core.

**Itens:**
1. `[sequencial]` [proto_fields.js](../../whatsbot-pro-plugins/plugins/protocolos/src/static/proto_fields.js) (módulo **puro**): `defaultValueFor(def)` (normaliza o padrão por tipo, espelhando `seedProtocolValues`, [:69](../../whatsbot-pro-plugins/plugins/protocolos/src/static/proto_fields.js#L69)) e `coerceNonEmpty(def, v)` → devolve o padrão quando `v` está vazio **e** a def tem padrão; senão devolve `v` intacto.
2. `[sequencial]` [resolve_form.js:55-108](../../whatsbot-pro-plugins/plugins/protocolos/src/static/resolve_form.js#L55-L108) (`FieldInput`): embrulhar o `onChange` com `coerceNonEmpty`. Como `LabeledField` é compartilhado (§2 #11), isso cobre o popup **e** o modal do protocolo de uma vez.
3. `[paralelo]` Aplica-se aos tipos com opção (`select`, `radio`, `checkboxes`) e ao `checkbox`. Em `text`/`textarea`/`number`/`date` o padrão só pré-preenche — apagar continua permitido (a não ser que o campo seja Obrigatório). Ver **P3**.
4. `[paralelo]` ⚠️ A coerção é **no `onChange`**, nunca na semeadura: um valor já salvo em branco (protocolo antigo) precisa continuar em branco até alguém escolher (D5).
5. `[paralelo]` Testes `node --test` dos dois helpers.
6. `[paralelo]` ⚠️ **Escopo desta fase = formulários.** O arrastar para "Sem valor" do Kanban (§2 #21) grava pela rota `POST /protocolos/{id}/set-field`, **não** passa pelo `FieldInput` e continua limpando o campo. Isso é a P6 — **não "conserte" por conta própria**: com a D6 em vigor, esse arrasto vira a única forma de esvaziar um campo com padrão, e pode ser exatamente o que se quer.
7. `[paralelo]` ⚠️ Registrar no comentário do `coerceNonEmpty` a consequência da D6: campo com padrão **nunca mais casa** a condição *"está vazio"* do construtor de regras do `retornos` (§2 #23) — para ninguém "simplificar" a regra depois sem saber o que ela sustenta.

**Pronto quando:** no popup e no modal do protocolo, abrir o seletor de "Temperatura" e clicar em "Limpar seleção" volta para **Frio** (nunca vazio); um protocolo antigo sem valor **continua** exibindo vazio até alguém escolher.

#### Status de execução — Fase 6
**Estado:** ✅ Concluída (2026-08-05)
- **O que foi feito:** `defaultValueFor(def)`, `coerceNonEmpty(def, v)` e `isBlankValue(def, v)` no módulo puro `proto_fields.js`; o `onChange` do `FieldInput` (`resolve_form.js`) passou a envolver o valor com `coerceNonEmpty`.
- **Como foi feito / decisões:** **P3 = (a)** — a regra vale para `select`, `radio` e `checkboxes`, os tipos que oferecem um estado vazio explícito na UI (o rodapé "Limpar seleção" do `OptionListSelect` chama `onChange('')`/`([])`). `text`/`textarea`/`number`/`date` ficam de fora: repor o padrão a cada tecla apagada seria hostil. **`checkbox` também ficou de fora** — desvio consciente do item 3 do plano: ali "vazio" é `false`, que é um VALOR e não uma ausência, e aplicar a regra tornaria uma caixa com padrão marcado **impossível de desmarcar** (com o P7 o ponto virou acadêmico, já que checkbox não tem padrão nesta versão). Como o `LabeledField` é compartilhado, o `onChange` cobre o popup **e** o modal do protocolo de uma vez, **sem tocar no `OptionListSelect` do core**.
- **Problemas / pendências:** a coerção é no `onChange`, nunca na semeadura — valor já salvo em branco (protocolo antigo) continua em branco até alguém escolher (D5). ⚠️ **P6**: arrastar para a coluna "Sem valor" do Kanban **continua limpando** (terceira superfície, não passa pelo `FieldInput`) — adotada a recomendação (a): mantido, com o texto de confirmação trocado de *"Limpar «X»…"* para *"Deixar «X» sem valor…"*, para o gesto ficar consciente. É a única saída para o estado vazio, que tem significado no motor de regras do `retornos`.
- **Verificação:** `node --test` — vazio→padrão em single e multi, valor escolhido intacto, sem padrão continua esvaziando, e os tipos de fora (checkbox/texto/número/data) inalterados.

---

### F7 — Testes, documentação, versão e zip (🔴 sozinha)

**Objetivo:** travar o comportamento, registrar o porquê e entregar o artefato.

**Itens:**
1. `[sequencial]` `tests/python/test_field_default_and_shortcut.py` (**já criado pela execução em curso — conferir cobertura em vez de recriar**): round-trip das 2 chaves; default inválido recusado; default órfão podado; protocolo novo nasce com o padrão; protocolo antigo **não** é preenchido; `/resolve` com `proto_fields` grava no protocolo; chave sem `show_on_resolve` ignorada; **campo de protocolo não vira atributo de conversa** (a D1, o teste mais importante do arquivo); campo obrigatório do protocolo fora do atalho **não** quebra o resolve (D7).
2. `[paralelo]` `tests/js/proto_fields.test.js`: `defaultValueFor` por tipo, `coerceNonEmpty` (vazio→padrão, não-vazio intacto, sem padrão→intacto), seed com/sem valor salvo.
3. `[paralelo]` [plugin.yaml](../../whatsbot-pro-plugins/plugins/protocolos/src/plugin.yaml): versão **1.27.0 sobre a 1.26.1** + parágrafo de `description` **ACRESCENTADO** ao da 1.26.1 (nunca por cima dele — o parágrafo da 1.26.1 documenta a regressão do `retornos_fields.py` e é o que impede alguém de repeti-la).
   ⚠️ O `<id>.json` **não é gerado por comando nenhum** — é editado à mão, e o `catalog.json` da raiz precisa ser bumpado junto, senão o builder recusa com `catalogue field protocolos.version is '…'; expected '…'`.
4. 🔴 `[sequencial]` **GUARDA OBRIGATÓRIA ANTES DO BUILD** — é aqui que a regressão da 1.26.0 nasceu, e o `--check` **não a pega** (ele compara o zip com a `src/`; `src/` incompleta gera zip incompleto que "confere"):
   ```
   test -f plugins/protocolos/src/retornos_fields.py            # o módulo existe na fonte?
   grep -c "filter.retornos" plugins/protocolos/src/filters.py  # deve ser >= 2
   ```
   E, **depois** do build: `unzip -l plugins/protocolos/protocolos.zip | grep retornos_fields` — o arquivo tem de estar DENTRO do artefato.

   🔴 **Por que esta guarda não é paranoia:** medido em 2026-08-05, **nenhum commit da fonte corresponde ao que roda em produção**. O `HEAD` da branch ainda é 1.26.0 e o zip commitado nele tem **zero** ocorrências de `retornos_fields.py` (`git show HEAD:plugins/protocolos/protocolos.zip` → 37 arquivos, sem o módulo). O conserto da 1.26.1 existe **só no disco** (working tree + cópia instalada + o zip publicado em `origin/master`). Portanto **o commit desta F7 é o primeiro da história a carregar o módulo** — se ele sair sem a guarda acima, a regressão volta ao artefato e o `--check` não vai avisar.
   ⚠️ Decisão que sobra para o dono do repo (**fora deste plano**): fazer ou não um commit de reconstituição da **1.26.1** antes da 1.27.0, para que o artefato publicado em `origin/master` tenha uma fonte auditável. Sem ele, um hotfix da 1.26.1 não tem base.
   ⛔ **NÃO use `rsync --delete` / `git checkout` / `git restore` sobre `src/` para "restaurar a 1.26.1"** enquanto houver trabalho não commitado na árvore — apaga a implementação em curso. Salve um patch antes (`git diff > wip.patch`).
5. `[sequencial]` Rebuild: `python3 scripts/build_plugins.py protocolos` + `--check`, **na branch `agent/publicar-plugins-plano-83`** (é a única que tem `src/` e `scripts/`; `origin/master` tem o layout antigo, só `<id>.json` + `<id>.zip`, e lá o builder não tem de onde buildar). Publicar para `master` depende de resolver a divergência de layout entre as branches — **trabalho FORA deste plano**.
   ⚠️ O zip regenerado terá o mesmo **conteúdo** do publicado, mas **não os mesmos bytes** (ordem das entradas/mtimes/permissões): o zip de produção não foi gerado por este builder. Não persiga igualdade binária — compare arquivo a arquivo.
6. `[sequencial]` **Instalar a cópia local antes de publicar** (`storages/plugins/protocolos/`) — a versão que roda é a instalada, não a do git; e testar na tela de verdade.
   ⚠️ **"Passou no dev" NÃO é evidência para os itens de regra** (§2 #24): este dev tem o `retorno_automatico`, não o `retornos`. Sem o consumidor instalado, `filter.retornos.campos`/`contexto` nunca são aplicados e o construtor de regras nem existe na tela. Para validar o impacto da F3/F6 nas réguas, instale o zip do `retornos` (existe em `origin/master` e em `assets/plugin_examples/retornos/`) ou valide em produção.
6. `[paralelo]` `CLAUDE.md`: **não** precisa de seção nova (o core não muda). Se algo entrar, é uma linha no bloco de plugins.
7. `[paralelo]` Rodar também `venv/bin/python -m pytest tests/integration tests/contracts` no core (nada deveria mudar — é a prova de que o plugin não vazou para lá).

**Pronto quando:** `python3 scripts/test_plugins.py protocolos` verde, suíte do core verde, zip reconstruído e a cópia instalada exercitada na interface.

#### Status de execução — Fase 7
**Estado:** ✅ Concluída (2026-08-05) — falta só o aceite manual na interface
- **O que foi feito:** `tests/python/test_field_default_and_shortcut.py` (22 testes: F0 + F1 + F3 + F4, incluindo os 2 de contrato HTTP real via `built.client`); 17 testes novos em `tests/js/proto_fields.test.js`; asserção de chaves ampliada em `test_field_descriptions.py`; `plugin.yaml` → **1.27.0** com o parágrafo de descrição; `_audit_field_defs` (routes.py) passou a resumir `atalho` e `tem_padrao` — marcar um atalho ou definir um padrão é config com dono e efeito daqui pra frente. ZIP reconstruído (`build_plugins.py protocolos` + `--check`: 38 arquivos, sha256 `bb56e370…`) com `protocolos.json` e `catalog.json` sincronizados, e a cópia **instalada** em `storages/plugins/protocolos/` (backup da 1.26.x no scratchpad da sessão).
- **Como foi feito / decisões:** a suíte do CORE (`tests/contracts`/`tests/integration`) foi **pulada a pedido do usuário** (pressa). A garantia que ela daria — "nada vazou para o core" — foi obtida direto e de graça: `git diff` do checkout do core está **vazio** (só o arquivo deste plano, não versionado). Nenhum arquivo do core foi tocado.
- **Problemas / pendências:** ⚠️ dois tropeços de PROCESSO, ambos meus e ambos corrigidos: (1) editei fontes com a suíte rodando, o que produziu 8 falhas fantasma; (2) subi um `pytest` do core **em paralelo** com o do plugin — os dois usam `WHATSBOT_TEST_DB_URL` e o segundo recria o schema, gerando `relation "plugins" does not exist` (3 erros de setup). Lição já registrada na memória do projeto: **nunca duas suítes ao mesmo tempo, nunca editar durante a corrida**. A re-execução limpa e serial deu verde.
- **Verificação:** `python3 scripts/test_plugins.py protocolos` ⇒ **160 passed / 0 failed** (pytest) + **56 pass / 0 fail** (`node --test`), `EXIT=0`. `build_plugins.py protocolos --check` ⇒ `current`. Servidor dev (porta 8090) recarregou e já serve os módulos novos (`coerceNonEmpty`/`seedProtoShortcut`/`changedValues` e `protoDefs`).

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| `normalize_values` no escopo protocolo (F4 item 2) | Exige **todos** os obrigatórios do escopo ([logic.py:446-450](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L446-L450)); um campo obrigatório do protocolo fora do atalho faria **todo resolve falhar** | Coagir chave a chave com `_coerce_extra`, sem o gate de required (D7). Teste dedicado na F7 item 1 |
| Concorrência popup × Kanban | Alguém arrasta o card ("Quente") enquanto o popup está aberto com "Frio" semeado; salvar reverteria | Gravar **só o que mudou** em relação ao seed (F4 item 6) |
| Protocolo legado ganhando `''` | Salvar o popup criaria linha de extra vazia num protocolo antigo, poluindo o que a limpeza por IA vai fazer | Mesma mitigação acima: chave não alterada não é enviada/gravada |
| Espelho no core | Um refactor futuro que generalize `sync_core_atendimento_defs` para os dois escopos quebraria a D1 em silêncio | Teste da F0 item 2 + F7 item 1 travando que o escopo protocolo **não** cria atributo de conversa |
| Materialização na criação (F3) | O ponto é chamado pelo handler de `message.saved`; uma exceção ali derruba o auto-vínculo | `try/except` + `logger.debug`, no padrão do arquivo; só roda quando `created` |
| Corrida de criação do protocolo | Dois inbounds simultâneos ⇒ `IntegrityError` no perdedor ([logic.py:873](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L873)) | Só o ramo `created` escreve padrão; o perdedor re-seleciona e não escreve |
| Default órfão | Renomear uma opção deixa o padrão apontando para valor inexistente ⇒ seletor mostra opção fantasma | Poda no PUT (F1 item 1), espelhando `_option_descriptions` ([logic.py:245](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L245)) |
| Opção com vírgula | `splitOptionList` divide por `[\n,]` ([proto_fields.js:119](../../whatsbot-pro-plugins/plugins/protocolos/src/static/proto_fields.js#L119)) — um padrão com vírgula nunca casaria a opção | Limitação conhecida e documentada (D6 do plano 102); o widget de padrão escolhe **da lista**, não digita |
| Só 1 dos 3 call sites | Atalho aparece pelo botão do chat mas não pelo Kanban ⇒ operador acha que "sumiu" | F5 item 6 cobre os três (§2 #15) |
| Popup mais longo | Com muitos campos marcados, o popup vira uma parede (o `max-h-[85vh] overflow-auto` já existe, [resolve_form.js:149](../../whatsbot-pro-plugins/plugins/protocolos/src/static/resolve_form.js#L149)) | A marcação é **opt-in** por campo; a seção só aparece se houver algum. Ver P4 |
| Modo escuro | Seção nova no popup + controles novos na tela Configurar | Só classes `wa-*`/`.wa-field`; conferir nos dois temas (F2 item 6, F5 item 8) |
| Postgres | `ON CONFLICT (owner, def_id)` já é o caminho usado ([logic.py:511](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L511)) | Nenhuma migration; nada de DDL novo |
| Fonte errada | Editar `assets/plugin_examples/protocolos/` (1.25.0) em vez da fonte (1.26.1) | §2 #20 — a fonte é `../whatsbot-pro-plugins/plugins/protocolos/src/`, **mas confira antes do build se ela não perdeu arquivo que só existe no espelho** |
| Publicar sem instalar | O que o usuário testa é `storages/plugins/protocolos/`; commit/zip não muda o que roda | F7 item 6 |
| 🔴 **Réguas do `retornos` mudam de resultado** | Os campos do protocolo são condições de regra (`protocolos.campo.<key>`) e o valor sai de `fields` (§2 #23). Materializar o padrão (F3) faz *"está vazio"* **parar de casar** e *"= Frio"* **passar a casar** em todo protocolo novo — mudando o disparo de follow-ups já salvos, **em silêncio** | **Revisar as réguas salvas ANTES de ligar o padrão de um campo.** A F3 é o gatilho, mas quem decide é quem liga o padrão na tela — vale um aviso no texto de ajuda da F2. Item no checklist §8 |
| 🔴 **"está vazio" fica inalcançável** | D6 + F6 tornam impossível esvaziar um campo com padrão por formulário; o `retornos` expõe *"está vazio"* como operador sobre ele | Consequência **aceita e registrada** na D6. A válvula de escape que resta é o arrastar para "Sem valor" (§2 #21) — ver **P6** |
| Arrastar para "Sem valor" | Terceira superfície de escrita, fora do `FieldInput`: continua limpando o campo mesmo com a D6 (§2 #21) | Decidir de propósito em **P6** — não deixar acontecer por omissão |
| Falso negativo no teste da F3 | O card novo demora até 30 s para entrar no índice do Kanban, porque o nascimento não bumpa a geração (§2 #22) | Critério de aceite da F3 reescrito com as 3 formas confiáveis de conferir |
| Tipo do `checkbox` no `retornos` | O extra grava `bool`; o catálogo de regras declara `checkbox` como `enum` `sim`/`nao` — regra "= sim" compara `True == "sim"` e nunca casa. **Pré-existente**, mas a F3 o generaliza para todo protocolo novo | **P7**: excluir `checkbox` do widget de padrão nesta versão, ou travar o formato gravado por teste |
| 🔴 **Rebuild reintroduz a regressão** | A 1.26.0 foi publicada sem o `retornos_fields.py`; o `--check` **não acusa** (compara zip × `src/`) | Guarda obrigatória na F7 item 4 (`test -f` + `grep` + `unzip -l`) e item no checklist |
| Execução concorrente | O plano está sendo executado por outra sessão enquanto é auditado; um build feito no meio empacota WIP sob o número de um bugfix | Não buildar com a árvore suja; conferir `git status` antes da F7 |

---

## 6. Perguntas em aberto

**P1 — No escopo `atendimento`, o valor padrão materializa ou só semeia o formulário?**
✅ **DECIDIDO (2026-08-05): só semeia o formulário.** O popup do atendimento **nasce em branco por design** (1.23.0, §2 #8) e é preenchido a cada resolução; materializar na criação do ciclo ([logic.py:2964](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L2964)) seria sobrescrito pelo próprio formulário segundos depois, e ainda escreveria linhas para ciclos que nunca são resolvidos. Note que semear com uma **constante** não reintroduz o bug da 1.23.0 — aquele era herança do **valor anterior**, coisa diferente.
Resumo da regra, para o executor não confundir:

| Escopo | Onde o padrão entra | Entidade que já existe sem valor |
|---|---|---|
| `protocolo` | gravado na **criação** do protocolo (F3) | fica vazia — não é semeada (D5) |
| `atendimento` | **semeia** o popup a cada resolução (F5/F6) | n/a — o popup nasce em branco por design |

**P2 — Ler os valores do protocolo com 2 chamadas existentes ou 1 endpoint novo?**
⏸️ **DECIDIR NA F5.**
(a) **Reusar** `GET /field-defs?scope=protocolo` + `GET /contacts/{cid}/protocolo` ([routes.py:241](../../whatsbot-pro-plugins/plugins/protocolos/src/routes.py#L241)) — zero superfície nova, ambos já gateados por `view`, o `contactIdOf` já existe. Custo: 2 requisições enquanto o popup abre (em paralelo com a que já existe).
(b) Endpoint novo `GET /conversas/{id}/resolve-form` devolvendo defs dos dois escopos + valores + `protocolo_id` numa tacada. Uma requisição, porém uma rota a manter.
**Recomendação:** (a). Se a latência de abertura do popup incomodar em produção, (b) vira um refactor isolado depois.

**P3 — A regra "nunca vazio" (D6) vale para campo de TEXTO com padrão?**
⏸️ **CONFIRMAR NA F6.** Em lista/rádio/caixa a regra é natural (o "Limpar seleção" vira "voltar ao padrão"). Em `text`/`textarea`/`number`/`date`, bloquear o apagar significaria repor o padrão a cada tecla apagada — hostil.
(a) **Regra só nos tipos com opção + `checkbox`**; em texto o padrão pré-preenche e pode ser apagado (a não ser que seja Obrigatório).
(b) Regra em todos os tipos, repondo o padrão quando o campo fica vazio ao perder o foco.
**Recomendação:** (a) — é a leitura do pedido ("não permitir que o usuário coloque algo vazio" foi dito sobre a Temperatura, um campo de lista).

**P4 — Limitar quantos campos podem ser marcados como atalho?**
⏸️ **ADIADO.** Hoje nada impede marcar os 5 campos do protocolo e transformar o popup numa parede. Não vale inventar limite antes de ver o uso; se incomodar, um aviso na tela Configurar ("N campos no popup de resolver") resolve sem regra rígida.

**P6 — Arrastar para "Sem valor" no Kanban continua limpando um campo COM padrão?** 🆕 *(descoberto em 2026-08-05)*
⏸️ **DECIDIR — a D6 não previu esta superfície.** O Kanban agrupado por campo de opção sempre renderiza a coluna `__none__` "Sem valor", ela **aceita drop** e o handler manda `value: null`, com a confirmação *"Limpar «Temperatura» do protocolo de X?"* ([protocolos_tab.js:405-413](../../whatsbot-pro-plugins/plugins/protocolos/src/static/protocolos_tab.js#L405-L413) → [logic.py:2993](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L2993)). Isso **não** passa pelo `FieldInput` da F6, então sobrevive à D6.
(a) **Deixar como está** — o arrasto vira a válvula de escape explícita para esvaziar (a única, depois da D6), e continua sendo a forma de fazer a régua *"está vazio"* do `retornos` voltar a casar. Custo: a promessa "nunca vazio" tem uma exceção, e ela não está escrita em lugar nenhum da UI.
(b) **Esconder a coluna "Sem valor" como alvo** quando o campo tem padrão (continua existindo para MOSTRAR os legados, só recusa o drop). Coerente com a D6, mas tira a única saída para o vazio.
(c) **Arrastar para "Sem valor" grava o PADRÃO** em vez de limpar. Máxima coerência com a D6 — e a coluna passa a ser um destino que nunca recebe card, o que confunde.
**Recomendação:** (a), **documentada**: trocar o texto de confirmação de *"Limpar…"* para algo como *"Deixar «Temperatura» sem valor no protocolo de X?"*, para o gesto ficar consciente. É a única opção que preserva o vazio como estado alcançável — e o vazio tem significado de negócio no motor de regras.

**P7 — O tipo `checkbox` deve ganhar valor padrão nesta versão?** 🆕 *(descoberto em 2026-08-05)*
⏸️ **DECIDIR ANTES DA F2.** O extra `checkbox` é gravado como `bool` Python ([logic.py:384-385](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L384)), mas o catálogo de regras do `retornos` declara `checkbox` como `enum` com opções `sim`/`nao` ([retornos_fields.py:58](../../whatsbot-pro-plugins/plugins/protocolos/src/retornos_fields.py#L58) e [:85-86](../../whatsbot-pro-plugins/plugins/protocolos/src/retornos_fields.py#L85)). Uma regra "= sim" compara `True == "sim"` e **nunca casa**. O descasamento é **pré-existente** e não é causado por este plano — mas hoje ele só é alcançável depois que alguém salva o formulário; com a F3, passa a valer para **todo protocolo novo** cujo checkbox tenha padrão "marcado".
(a) **Excluir `checkbox` do widget de padrão** nesta versão (uma linha a menos na F2). O descasamento continua onde está, sem ser amplificado.
(b) Incluir, com um teste na F7 travando o FORMATO gravado — e abrir um item separado para consertar o descasamento no `retornos_fields.py` (que é de OUTRO plano: é o lado do `retornos`).
**Recomendação:** (a). Um checkbox com padrão "marcado" é o caso de menor valor da feature e o de maior chance de efeito colateral silencioso.

**P5 — O atalho deve valer também quando a continuidade funde no protocolo anterior?**
⏸️ **DECIDIR NA F5.** No caminho `outcome === 'resolved'` ([extends.js:190](../../whatsbot-pro-plugins/plugins/protocolos/src/static/extends.js#L190)) o popup **não abre** — nenhum `/resolve` acontece e o protocolo anterior já está finalizado. Coerente com a D5 ("o que está fechado fica como está"), então o comportamento esperado é: nesse caminho, o atalho simplesmente não aparece. **Registrar como esperado**, para ninguém tratar como bug depois.

---

## 7. Apêndice — arquivos-chave

| Camada | Arquivo | Papel |
|---|---|---|
| Plugin · backend | [logic.py:211-223](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L211-L223), [:245-348](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L245-L348) | **edita** — `get_extra_defs` / `_normalize_extra_def` / `set_field_defs`: as 2 chaves novas (F1) |
| Plugin · backend | [logic.py:841-880](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L841-L880) | **edita** — `ensure_protocolo_for_contact`: nascer com os padrões (F3) |
| Plugin · backend | [logic.py:2985-3046](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L2985-L3046) | **edita** — `resolve_atendimento`: `proto_values` (F4) |
| Plugin · backend | [logic.py:504-515](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L504-L515) | **reusa sem tocar** — `upsert_extra` já é genérico por escopo |
| Plugin · backend | [logic.py:654-745](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L654-L745) | **não mexer** — o espelho no core é do escopo `atendimento`; é o que garante a D1 |
| Plugin · backend | [logic.py:4706-4740](../../whatsbot-pro-plugins/plugins/protocolos/src/logic.py#L4706-L4740) | **não mexer** — `_check_before_status` (D7) |
| Plugin · rotas | [routes.py:601-609](../../whatsbot-pro-plugins/plugins/protocolos/src/routes.py#L601-L609) | **edita** — repassar `proto_fields` (F4) |
| Plugin · rotas | [routes.py:621-635](../../whatsbot-pro-plugins/plugins/protocolos/src/routes.py#L621-L635) | inalterado — o `PUT` já carrega a def inteira |
| Plugin · backend | [retornos_fields.py](../../whatsbot-pro-plugins/plugins/protocolos/src/retornos_fields.py) | 🔴 **NÃO MEXER — mas GARANTIR que entra no zip** (F7 item 4). Consome `get_field_defs` (definições) e `entidade["fields"]` (valores): a F1 é transparente para ele, mas **F3 e F6 mudam o que ele enxerga** (§2 #23) |
| Plugin · backend | [filters.py](../../whatsbot-pro-plugins/plugins/protocolos/src/filters.py) | 🔴 **NÃO MEXER** — registra `filter.retornos.campos`/`contexto`. Se sumir do zip, o construtor de regras do `retornos` perde os grupos "Protocolos" e "Protocolos · Atendimento" **e as réguas salvas passam a avaliar como falsas** (foi a regressão da 1.26.0) |
| Plugin · frontend | [static/protocolos_tab.js:400-415](../../whatsbot-pro-plugins/plugins/protocolos/src/static/protocolos_tab.js#L400-L415) | **só se a P6 for (b)/(c)** — `onDrop` da coluna "Sem valor" (§2 #21) |
| Plugin · frontend puro | [static/proto_fields.js](../../whatsbot-pro-plugins/plugins/protocolos/src/static/proto_fields.js) | **edita** — `defaultValueFor`, `coerceNonEmpty` (F6) |
| Plugin · frontend | [static/resolve_form.js:55-189](../../whatsbot-pro-plugins/plugins/protocolos/src/static/resolve_form.js#L55-L189) | **edita** — seção "Protocolo" (F5) + coerção do `onChange` (F6) |
| Plugin · frontend | [static/extends.js:49-75](../../whatsbot-pro-plugins/plugins/protocolos/src/static/extends.js#L49-L75), [:177-253](../../whatsbot-pro-plugins/plugins/protocolos/src/static/extends.js#L177-L253) | **edita** — buscar defs/valores do protocolo e enviar `proto_fields` (F5) |
| Plugin · frontend | [static/config.js:175](../../whatsbot-pro-plugins/plugins/protocolos/src/static/config.js#L175), [:187-204](../../whatsbot-pro-plugins/plugins/protocolos/src/static/config.js#L187-L204), [:308-409](../../whatsbot-pro-plugins/plugins/protocolos/src/static/config.js#L308-L409) | **edita** — controles do atalho e do padrão (F2) |
| Plugin · frontend | [static/protocolos_tab.js:1141-1156](../../whatsbot-pro-plugins/plugins/protocolos/src/static/protocolos_tab.js#L1141-L1156), [:1815](../../whatsbot-pro-plugins/plugins/protocolos/src/static/protocolos_tab.js#L1815) | **edita** — 3º call site do popup (F5); o modal herda a F6 pelo `LabeledField` |
| Plugin · manifest | [plugin.yaml](../../whatsbot-pro-plugins/plugins/protocolos/src/plugin.yaml) | **edita** — versão 1.27.0 + descrição (F7) |
| Plugin · testes | `tests/python/test_field_default_and_shortcut.py` (novo), [tests/js/proto_fields.test.js](../../whatsbot-pro-plugins/plugins/protocolos/tests/js/proto_fields.test.js), [tests/python/test_field_descriptions.py](../../whatsbot-pro-plugins/plugins/protocolos/tests/python/test_field_descriptions.py) | **edita/cria** — F0 e F7 |
| Core | [web/static/js/components/OptionListSelect.js](../web/static/js/components/OptionListSelect.js) | **NÃO MEXER** — a D6 é resolvida do lado do plugin |
| Core | qualquer arquivo | **NÃO MEXER** — este plano é 100% plugin |

---

## 8. Checklist de verificação

- [ ] `PUT`/`GET /field-defs` preservam `show_on_resolve` e `default_value` (round-trip)
- [ ] Def **legada** (sem as chaves) continua carregando, com os defaults do `setdefault`
- [ ] `default_value` fora das opções é **recusado** com mensagem citando o rótulo
- [ ] Opção renomeada ⇒ default órfão é **podado** no mesmo PUT
- [ ] `show_on_resolve` marcado no escopo `atendimento` é **forçado a `False`** pelo servidor
- [ ] Contato novo ⇒ a linha nasce em `plugin_protocolos_protocolo_extras` com "Frio" (conferir no banco, **não** só no Kanban — §2 #22: o card leva até 30 s para entrar no índice)
- [ ] Os protocolos **antigos** continuam em "Sem valor" (nada de backfill)
- [ ] `retornos_fields.py` está na `src/` **e dentro do zip** (`unzip -l … | grep retornos_fields`)
- [ ] `filters.py` da `src/` registra os 2 seams (`grep -c "filter.retornos" … >= 2`)
- [ ] Réguas do `retornos` que usam `protocolos.campo.*` foram **revisadas** antes de ligar o padrão de um campo (a condição *"está vazio"* muda de significado — §5)
- [ ] P6 decidida e o comportamento do arrastar para "Sem valor" está conforme a decisão
- [ ] P7 decidida (o `checkbox` tem ou não widget de valor padrão)
- [ ] `git status` do repo de plugins **limpo do que não é deste plano** antes de buildar (não empacotar WIP sob número de bugfix)
- [ ] Build feito na branch `agent/publicar-plugins-plano-83` (a única com `src/` + `scripts/`)
- [ ] Protocolo **fechado** não é tocado por nenhum caminho deste plano
- [ ] Popup de resolver mostra a seção "Protocolo" com o valor **atual**, nos **3** call sites
- [ ] Trocar o valor no popup move o card no Kanban sem reload (WS `_broadcast_changed`)
- [ ] `conversations.custom_attributes` **não** ganha a chave do campo de protocolo
- [ ] `custom_attribute_definitions (applies_to=conversation)` **não** ganha linha nova
- [ ] Campo **obrigatório** do protocolo fora do atalho **não** quebra o resolve (D7)
- [ ] Botão "Resolver" não ganhou trava nova (D7)
- [ ] "Limpar seleção" num campo com padrão volta ao padrão — no popup **e** no modal do protocolo
- [ ] Valor salvo em branco (protocolo antigo) **continua** em branco até alguém escolher
- [ ] Chave não alterada não é gravada (sem reverter mudança feita no Kanban durante o popup)
- [ ] Sem o plugin de campos marcados, o popup fica **byte-idêntico** ao de hoje
- [ ] Tela Configurar e popup legíveis no **modo escuro**
- [ ] `node --test` verde em `tests/js/`
- [ ] `python3 scripts/test_plugins.py protocolos` verde
- [ ] `venv/bin/python -m pytest tests/integration tests/contracts` verde no Postgres (`WHATSBOT_TEST_DB_URL`) — prova de que nada vazou para o core
- [ ] `git diff` do core **vazio**
- [ ] Zip reconstruído (`build_plugins.py protocolos` + `--check`) e a cópia **instalada** em `storages/plugins/protocolos/` exercitada na interface
