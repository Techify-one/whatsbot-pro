# Plano de Implementação — 15: Correção do "Salvar" no painel Dados do contato

> Um testador relatou que, ao clicar **Salvar** no painel **Dados do contato** (`ContactInfoPanel`), três
> coisas falham: (1) o campo **Endereço** não persiste; (2) texto digitado em **Tags**/**Observações** mas
> não confirmado é descartado; (3) quando o save falha, **nada** aparece para o usuário (só vai pro
> console). A investigação confirmou os 3 e encontrou **bugs adicionais** na mesma tela: campos escalares
> não podem ser **limpos**, atributos personalizados não podem ser **removidos**, e a falha de salvar
> tags é **silenciosamente engolida** (o painel finge sucesso e os dados somem no refresh).
>
> **Escopo:** corrigir o caminho de gravação do painel Dados do contato de ponta a ponta —
> (1) rota `PUT /api/contacts/{phone}/info` (endereço + semântica de limpar); (2) `ContactInfoPanel.js`
> (flush do texto pendente, feedback de erro, falha parcial de tags, limpar atributo personalizado);
> (3) cobertura em `tests/test_endpoints.py`.
>
> **Fora de escopo:** redesenho do painel; mudar a semântica de `ContactMemory.update_info` usada pelo
> **tool calling do LLM** (a regra "só sobrescreve não-vazio" é correta para auto-preenchimento da IA e
> deve continuar); painel de conversa (`ConversationInfoPanel`); o marcador `~` de pushName (ver §6, bug
> menor de baixa frequência, opcional).

---

## 0. Estado atual VERIFICADO (2026-06-21, working tree pós-`7fda567`)

> Tudo abaixo foi confirmado por leitura com âncora `arquivo:linha` + verificação adversarial
> (24 achados, 0 falsos positivos). **Na implementação, re-ancore por `grep` (nome de
> função/rota/campo), nunca por número de linha fixo** — os arquivos do painel são grandes e mudam.

### Caminho de gravação (como é hoje)

- **Painel** [`web/static/js/components/contacts/ContactInfoPanel.js`](../web/static/js/components/contacts/ContactInfoPanel.js):
  - `form` inclui `address` (init `:22`, populado de `info.address` `:47`, campo renderizado `:153`).
  - `handleSave` (`:132-146`) dispara em paralelo `updateContactInfo(phone, { ...form, custom_attributes })`
    e `updateContactTags(phone, tags)`; só chama `onSave` se `infoRes.ok`; `catch` só faz `console.error`.
  - `newObs` (`:25`) e `tagSearch` (`:32`) só entram em `form.observations`/`tags` via `addObservation`
    (`:85-90`, ligado a Enter `:373` e botão `+` `:379`) e `addTagToContact`/`handleCreateTag`
    (`:97-121`, `:249`, `:262-270`). **Nenhum é lido em `handleSave`.**
  - Não existe estado de erro (apenas `saving`); nenhum elemento de mensagem de erro no JSX.
  - `onChange` de atributo personalizado (`:338-343`) faz `delete next[key]` quando o valor é limpo.
- **Service** [`web/static/js/services/api.js`](../web/static/js/services/api.js):
  - `request()` (`:26-39`) **não lança** em 4xx/5xx; só trata 401. Para 400/403/500 retorna
    `res.json()` → `{ok:false, error}`. Logo o `catch` do painel é **morto** para erros de backend.
  - `updateContactInfo` (`:273`), `updateContactTags` (`:372`), `createTag` (`:360`).
- **Rota** [`server/routes/contacts.py`](../server/routes/contacts.py) `PUT /api/contacts/{phone}/info`
  (`:1149-1204`):
  - Docstring (`:1151-1152`) lista name/email/profession/company/observations/custom_attributes —
    **omite `address`**.
  - `_update()` (`:1175-1196`) chama `contact.update_info(name=…, email=…, profession=…, company=…)` —
    **`address` NUNCA é lido do body** (`grep address server/routes/contacts.py` → 0 ocorrências).
  - Observações são substituídas direto via `contact_repo.set_observations` (`:1186-1191`).
  - Atributos: `valid_partial` é montado só de `custom_attrs.items()` (`:1164-1171`) e gravado por
    `ca_repo.set_values` (`:1193`).
- **Modelo** [`agent/memory.py`](../agent/memory.py) `update_info` (`:377-390`): faz loop sobre
  `(name, email, profession, company, address)` mas só grava com `if val:` (`:382`) — **valor vazio é
  ignorado** (não dá pra limpar). `update_info` **suporta** `address` (`:380`), mas a rota não passa.
- **Repo** [`db/repositories/contact_repo.py`](../db/repositories/contact_repo.py):
  - `update(contact_id, **fields)` (`:131-137`) grava qualquer campo passado, **inclusive `""`** (sem
    guard de truthiness) → a barreira para limpar está só no `if val:` de `update_info`.
  - `get_full_contact` retorna `address` em `data["address"]` (`:554`) e `data["info"]["address"]`
    (`:535`) → leitura/refresh já funcionam; o furo é só na escrita.
- **Tags** [`db/repositories/tag_repo.py`](../db/repositories/tag_repo.py) `set_contact_tags` (`:83-96`):
  só vincula nomes que **já existem** na tabela `tags` (`if tag_id is not None` `:91`). **Nome
  desconhecido é silenciosamente descartado** — mas a rota
  [`server/routes/tags.py`](../server/routes/tags.py) (`:118-119`, `:161`) devolve `contact.tags =
  list(new_tags)`, ou seja, **ecoa o nome descartado como se tivesse salvo**.

### Sintomas confirmados (mapa bug → causa-raiz)

| # | Sintoma | Causa-raiz | Arquivo:linha |
|---|---------|-----------|---------------|
| 1 | Endereço não persiste | rota não lê `body["address"]` | [`contacts.py:1179-1184`](../server/routes/contacts.py#L1179-L1184) |
| 2a | Observação digitada e não confirmada some no Salvar | `handleSave` ignora `newObs` | [`ContactInfoPanel.js:132-146`](../web/static/js/components/contacts/ContactInfoPanel.js#L132-L146) |
| 2b | Tag digitada e não confirmada some no Salvar | `handleSave` ignora `tagSearch`; nome novo não vira tag global | [`ContactInfoPanel.js:132-146`](../web/static/js/components/contacts/ContactInfoPanel.js#L132-L146), [`tag_repo.py:91`](../db/repositories/tag_repo.py#L91) |
| 3 | Falha de save não aparece pro usuário | sem estado de erro; `request()` não lança em 4xx/5xx | [`ContactInfoPanel.js:132-146`](../web/static/js/components/contacts/ContactInfoPanel.js#L132-L146), [`api.js:26-39`](../web/static/js/services/api.js#L26-L39) |
| 4 | Não dá pra **limpar** name/email/profession/company/address | `if val:` em `update_info` pula vazio | [`memory.py:380-386`](../agent/memory.py#L380-L386) |
| 5 | Não dá pra **remover** atributo personalizado | painel faz `delete` da chave; `set_values` só remove com `None` explícito | [`ContactInfoPanel.js:338-343`](../web/static/js/components/contacts/ContactInfoPanel.js#L338-L343), [`custom_attribute_repo.py:155-176`](../db/repositories/custom_attribute_repo.py#L155-L176) |
| 6 | Tags falham mas info salva → painel finge sucesso, some no refresh | `onSave` é gated só em `infoRes.ok`, faz fallback pro `tags` local | [`ContactInfoPanel.js:140`](../web/static/js/components/contacts/ContactInfoPanel.js#L140), [`Contacts.js:1464-1470`](../web/static/js/components/contacts/Contacts.js#L1464-L1470) |

---

## 1. Backend — rota `PUT /api/contacts/{phone}/info` (resolve #1 e #4)

**Objetivo:** o painel é um formulário de edição **humana** → semântica de **substituir** (incluindo
limpar). Não reusar o `update_info` (merge do LLM) para os escalares.

1.1. **Novo método de escrita em `ContactMemory`** (não mexer no `update_info` existente — ele continua
para o tool calling do LLM). Em [`agent/memory.py`](../agent/memory.py), adicionar algo como
`set_info_fields(self, fields: dict)` que, **só para as chaves presentes** em `fields`, escreve o valor
**incondicionalmente** (tratando `""` como limpar): atualiza `self.info[key]` e chama
`contact_repo.update(self.id, **fields)`. Validar que `key` ∈ `{name,email,profession,company,address}`.

1.2. **Rota usa o novo método** em [`server/routes/contacts.py`](../server/routes/contacts.py) `_update()`
(`:1175-1196`): montar `scalar_fields` a partir das chaves **presentes no body** (não usar default `""`
que torna "ausente" indistinguível de "vazio" — usar `body.get(key, sentinel)` ou
`{k: body[k] for k in (...) if k in body}`), incluindo **`address`**, e chamar
`contact.set_info_fields(scalar_fields)`. Manter o restante (observações via `set_observations`,
atributos via `ca_repo.set_values`).

> ⚠️ **Distinguir "ausente" de "vazio".** O painel sempre manda todas as 5 chaves, então "limpar" =
> mandar `""`. Para robustez contra outros chamadores, só escrever a chave se ela vier no body. Decisão:
> **chave presente com `""` ⇒ limpar**; chave ausente ⇒ não tocar.

1.3. **Docstring**: incluir `address` na lista de campos.

1.4. O retorno (`info` em `:1196-1204`) já reflete `contact.info` atualizado — confirmar que `address`
atualizado aparece no retorno (com 1.1 ele estará em `self.info`).

---

## 2. Backend — remover atributo personalizado (resolve #5)

`ca_repo.set_values` é um **merge** (só apaga chave quando vem `None` explícito) e é usado pelo tool
`set_custom_attribute` do LLM para escrita de chave única — **não mudar esse contrato**.

2.1. **Tratar `None` na rota.** Em [`server/routes/contacts.py`](../server/routes/contacts.py) no loop de
validação (`:1164-1171`): se `value is None`, **pular `validate_value`** e colocar `valid_partial[key] =
None` (sinaliza remoção para `set_values`). Assim o front pode mandar `null` para apagar.

2.2. **Front manda `null` em vez de remover a chave.** Em
[`ContactInfoPanel.js`](../web/static/js/components/contacts/ContactInfoPanel.js) `onChange` do
`CustomAttributeField` (`:338-343`): quando limpo, **`next[key] = null`** em vez de `delete next[key]`
(só para chaves que existiam em `info.custom_attributes`; chave nunca setada pode continuar sendo omitida).

> **Alternativa** (se preferir manter o front como está): a rota faz o **diff** entre os atributos atuais
> e os enviados e manda `None` para as chaves que sumiram. Mais robusto (semântica de replace real para o
> painel), porém muda o comportamento para qualquer chamador que mande `custom_attributes` parcial. Como
> hoje **só o painel** usa `PUT /info` com `custom_attributes`, ambas são seguras; a 2.1+2.2 tem menor
> raio de impacto.

---

## 3. Frontend — flush do texto pendente no Salvar (resolve #2)

Em [`ContactInfoPanel.js`](../web/static/js/components/contacts/ContactInfoPanel.js) `handleSave`
(`:132-146`), **antes** de montar o payload:

3.1. **Observações:** se `newObs.trim()` não vazio e ainda não está em `form.observations`, anexar à lista
enviada. Enviar `observations` já com o pendente; limpar `newObs` no sucesso.

3.2. **Tags:** se `tagSearch.trim()` não vazio:
- se casa (case-insensitive) com uma tag **global existente** → adicionar à lista `tags` enviada;
- se **não existe** → **criar a tag global primeiro** (`createTag(nome, corDefault)`), e só então
  adicioná-la — senão o backend descarta o nome silenciosamente
  ([`tag_repo.py:91`](../db/repositories/tag_repo.py#L91)). Reaproveitar a lógica de `handleCreateTag`
  (`:109-121`).
- limpar `tagSearch` no sucesso.

> Como `createTag` é assíncrono e pode falhar, fazer isso **antes** do `Promise.all` (ou encadear) e
> tratar o erro junto com o feedback da §4. Escolher uma cor default (ex.: `TAG_COLORS[0]`) ou a próxima
> não usada.

3.3. **Alternativa de UX mínima** (se não quiser auto-criar): deixar o trap óbvio — desabilitar **Salvar**
ou mostrar dica enquanto `newObs`/`tagSearch` tiverem texto não confirmado. A opção 3.1/3.2 (flush) é a
preferida porque "digitar e salvar" é o comportamento que o testador esperava.

---

## 4. Frontend — feedback de erro + falha parcial de tags (resolve #3 e #6)

Em [`ContactInfoPanel.js`](../web/static/js/components/contacts/ContactInfoPanel.js):

4.1. **Estado de erro.** Adicionar `const [error, setError] = useState(null)` (seguir o padrão já usado em
[`CustomAttributesManager.js`](../web/static/js/components/CustomAttributesManager.js) e
[`QuickReplies.js`](../web/static/js/components/QuickReplies.js)). Limpar no início de `handleSave`.

4.2. **Gate em ambos.** Só chamar `onSave` quando **`infoRes.ok && tagsRes.ok`**. Se qualquer um falhar,
**não** fechar o painel e **não** fazer fallback para o `tags` local (mentira de sucesso, bug #6). Setar
`error` com `infoRes.error || tagsRes.error || 'Falha ao salvar'`.

4.3. **Catch também seta erro** (rede/parse): `setError('Falha ao salvar. Tente novamente.')` além do
`console.error`.

4.4. **Render.** Mostrar uma região de erro temada (classes `wa-*` / `text-red-*` com fallback dark do
`custom.css`) perto do botão **Salvar** (`:391-400`). Testar no **modo escuro** (regra do CLAUDE.md).

> Não é preciso mexer em `request()` ([`api.js:26-39`](../web/static/js/services/api.js#L26-L39)) — o
> contrato `{ok:false, error}` é intencional; o conserto é consumir `error` no painel.

---

## 5. Testes — `tests/test_endpoints.py` (cobre o aceite)

O único teste de escalares do `PUT /info` ([`:602-618`](../tests/test_endpoints.py#L602-L618)) **não
toca `address`** (0 ocorrências de `address` no arquivo). Adicionar:

5.1. **Endereço round-trip** (ficaria **vermelho hoje**): `PUT /info` com `address` → `GET` e asserir
`data["address"]` **e** `data["info"]["address"]`.

5.2. **Limpar escalar:** setar `name`/`address` não vazio, depois `PUT` com `""`, asserir que o `GET`
retorna vazio (cobre #4).

5.3. **Observações por conteúdo, não só `len`:** trocar o `len(...) == 2` por
`set(observations) == {"VIP client", "Prefers morning calls"}` (substituição exata); adicionar caso com
observação só de espaços e asserir que é filtrada.

5.4. **Remover atributo personalizado:** setar um atributo, depois `PUT` com aquele campo `null` (ou
omitido, conforme a decisão da §2) e asserir que sumiu do `GET` (cobre #5).

5.5. **Contrato de erro:** para um `PUT /info` (403/404) e um `PUT /tags` (404), asserir
`r.json()["ok"] is False` **e** `isinstance(r.json().get("error"), str)` **e** `error != ""` (metade
backend do #3).

5.6. **Tags — borda:** `PUT /tags` com nome **inexistente** na tabela `tags` → documentar/asserir o
comportamento (após a §3.2, criar-antes-de-vincular; ou asserir que é descartado se mantido o atual).

5.7. **Aceite E2E:** um único save lógico com name+email+profession+company+**address**+observations+
custom_attributes, depois `PUT /tags`, depois **um** `GET` asserindo que **tudo** persiste.

> Frontend: os bugs #2/#3/#6 são de wiring do painel e não são cobertos pelo `TestClient`. Validar
> manualmente (ver §7) — ou, se houver harness de e2e de UI, adicionar lá.

---

## 6. (Opcional) Bug menor — nome iniciado por `~`

[`ContactInfoPanel.js:43`](../web/static/js/components/contacts/ContactInfoPanel.js#L43) tira um `~`
inicial **só na carga** (é o marcador de pushName automático). Para um nome real tipo `~Lulu`, reabrir +
salvar derruba o `~` permanentemente. **Baixíssima frequência** e fora dos 3 reportados — incluir só se
quiser robustez total. Fix: não tratar como auto-name por `startsWith("~")` cego; ou nunca round-tripar o
`~` pelo input editável (deixar o backend dono do marcador).

---

## 7. Validação manual (aceite)

1. Abrir uma conversa → **Dados do contato**.
2. Preencher **Endereço**, digitar uma **observação** (sem Enter/`+`), digitar uma **tag** nova (sem
   selecionar no dropdown) → **Salvar**.
3. Fechar o painel, reabrir e dar **refresh (F5)** → os 3 devem persistir.
4. **Limpar** um campo (ex.: Empresa) e salvar → reabrir/refresh → deve ficar vazio.
5. **Remover** um atributo personalizado e salvar → reabrir/refresh → deve sumir.
6. Forçar um erro (ex.: sem permissão `contact.write`, ou backend off) → **mensagem visível** no painel,
   painel **não** fecha.
7. Repetir tudo no **modo escuro** conferindo contraste da mensagem de erro.

---

## 8. Resumo dos arquivos tocados

| Arquivo | Mudança |
|---------|---------|
| [`agent/memory.py`](../agent/memory.py) | novo `set_info_fields` (escrita incondicional por chave presente) — não mexer no `update_info` do LLM |
| [`server/routes/contacts.py`](../server/routes/contacts.py) | `PUT /info`: ler `address` + usar `set_info_fields`; tratar `None` em custom_attributes; docstring |
| [`web/static/js/components/contacts/ContactInfoPanel.js`](../web/static/js/components/contacts/ContactInfoPanel.js) | flush `newObs`/`tagSearch` (auto-criar tag); estado+render de erro; gate `infoRes.ok && tagsRes.ok`; `null` ao limpar atributo |
| [`tests/test_endpoints.py`](../tests/test_endpoints.py) | testes 5.1–5.7 |

> **Ordem sugerida:** §1 → §5.1/5.2/5.7 (provar backend) → §2 → §3 → §4 → demais testes → §6 (opcional).
> Rodar `python tests/test_endpoints.py` ao fim de cada bloco backend.
