# Plano 77 — Atributo órfão (`cw_id`) não pode mais bloquear o salvamento de contato

> **Status:** EM EXECUÇÃO (Fases A+B ✅ verdes · Fase C pendente, operacional) · **Data:** 2026-07-23 · **Escopo:** pequeno
> **Origem:** Bug reportado pelo usuário — ao salvar o nome de um contato em produção o painel devolve `{"ok": false, "error": "Atributo 'cw_id' não existe."}` (não é permissão; reproduzido com admin e atendente). **Método:** leitura do código real (`arquivo:linha`) + inspeção do banco de produção `whatsbot` via MCP vault.
> O salvamento inteiro do contato (nome, email, observações, tags) é abortado com **HTTP 400** por causa de uma chave de atributo personalizado herdada da migração Chatwoot (`cw_id`, `cw_identifier`) que existe em `contacts.custom_attributes` mas **não tem definição** em `custom_attribute_definitions`. O frontend reenvia o JSON inteiro no save → a validação backend rejeita a chave órfã antes de gravar qualquer campo. A correção tem 3 frentes: hardening do backend (tolerar chaves órfãs já armazenadas), correção do frontend (só enviar chaves definidas — igual ao painel de conversa que já faz certo) e limpeza de dados (endpoint de purge já existe).
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | Corrigir nas **3 frentes** propostas: backend tolera órfãos + frontend descarta órfãos + limpeza de dados | Fases A (backend), B (frontend), C (dados). Redundância é intencional (defesa em profundidade) |
| D2 | ✅ (2026-07-23) O bug é reproduzível e mede-se em prod: **14.478 de 14.604 contatos** têm `cw_id` | Qualquer contato importado do Chatwoot dispara o erro. Prioridade alta, mas mudança pequena |
| D3 | Preservar a semântica P50 (chave **genuinamente inexistente e nova** = erro) onde ela protege contra typo de código | O backend só tolera chave órfã **que já está no JSON armazenado** — não abre a porta para qualquer chave arbitrária. Ver P1 |

---

## 1. Resumo executivo

O `PUT /api/contacts/{phone}/info` valida cada chave de `custom_attributes` contra as definições ativas/soft-deleted **antes** de gravar qualquer coisa ([server/routes/contacts.py:2240-2260](../server/routes/contacts.py#L2240)). Uma chave sem definição alguma (nem soft-deleted) devolve **400**, abortando o save inteiro. O painel do contato reenvia o `custom_attributes` **completo** (incluindo `cw_id`/`cw_identifier` herdados do Chatwoot) a cada save ([ContactInfoPanel.js:61,182](../web/static/js/components/contacts/ContactInfoPanel.js#L61)). O painel de **conversa** já resolveu esse mesmo problema enviando **apenas as chaves definidas** ([ConversationInfoPanel.js:133-147](../web/static/js/components/contacts/ConversationInfoPanel.js#L133)) — vamos portar esse padrão para o contato, endurecer o backend para tolerar órfãos já armazenados, e limpar os dados via o endpoint de purge que **já existe** (`POST /api/custom-attributes/purge-orphans`).

---

## 2. Como funciona hoje (mapa)

| Camada | Local | Comportamento atual |
|--------|-------|---------------------|
| Backend contato | [server/routes/contacts.py:2242-2267](../server/routes/contacts.py#L2242) | Valida `custom_attributes` up-front. `defs` = definições ativas; `known_keys` = ativas **+ soft-deleted**. Chave em `known_keys` mas sem def ativa → tolerada (`continue`). Chave fora de `known_keys` → `return _err(f"Atributo '{key}' não existe.", 400)` (linha 2260). ⚠️ Roda **antes** de qualquer escrita ("before touching the row") — por isso o nome nem chega a ser gravado |
| Backend conversa | [server/routes/conversations.py:702-724](../server/routes/conversations.py#L702) | Validação **idêntica** (mesmo `return _err(f"Atributo '{key}' não existe.", status=400)` na linha 715) |
| Frontend contato (bugado) | [ContactInfoPanel.js:61](../web/static/js/components/contacts/ContactInfoPanel.js#L61) e [:182](../web/static/js/components/contacts/ContactInfoPanel.js#L182) | `setCustomValues({ ...(info.custom_attributes || {}) })` copia **tudo** que está no JSON armazenado (inclui órfãos). No save envia `custom_attributes: customValues` inteiro. O render só itera `customDefs` ([:405](../web/static/js/components/contacts/ContactInfoPanel.js#L405)), mas os órfãos continuam no state e são reenviados |
| Frontend conversa (correto) | [ConversationInfoPanel.js:141-147](../web/static/js/components/contacts/ConversationInfoPanel.js#L141) | `buildConvAttrsPayload()` monta o payload iterando **só `convDefs`** — órfãos nunca saem do cliente. Por isso o painel de conversa **não** dispara o bug |
| Repo (escrita) | [db/repositories/custom_attribute_repo.py:212+](../db/repositories/custom_attribute_repo.py#L212) `set_values` | MERGE: reatribui o dict inteiro; `None` remove a chave |
| Repo (limpeza) | [db/repositories/custom_attribute_repo.py:175-201](../db/repositories/custom_attribute_repo.py#L175) `purge_orphan_values` | **Já existe**: remove de cada entidade as chaves sem def ativa. Retorna nº de linhas tocadas |
| Endpoint de limpeza | [server/routes/custom_attributes.py:141-149](../server/routes/custom_attributes.py#L141) `POST /api/custom-attributes/purge-orphans?applies_to=contact` | **Já existe**: chama `purge_orphan_values`. Basta invocar |

**Evidência do banco de produção (`whatsbot`):**

| Consulta | Resultado |
|----------|-----------|
| Definições `contact` | `address, company, cpf, email, profession` — **sem `cw_id`, sem `cw_identifier`** |
| Definições `conversation` | `motivo, obs, oferta_atual, perfil_cliente, resultado, teste` |
| Chaves distintas em `contacts.custom_attributes` | `cpf, cw_id, cw_identifier, email, profession` → `cw_id` e `cw_identifier` são **órfãs** |
| Contatos com `cw_id` | **14.478** de **14.604** total |

---

## 3. Inventário / análise

| # | Item | Local | O que falta | Abordagem | Risco | Esforço |
|---|------|-------|-------------|-----------|-------|---------|
| 1 | Backend contato tolera chave órfã já armazenada | [contacts.py:2251-2260](../server/routes/contacts.py#L2251) | Hoje só tolera soft-deleted (`known_keys`). Precisa também tolerar chave presente no JSON armazenado do contato | Carregar o `custom_attributes` armazenado do contato up-front e, para chave fora de `known_keys` **mas presente no armazenado**, tolerar (`continue`) em vez de 400 | Baixo | S |
| 2 | Backend conversa: mesmo hardening | [conversations.py:711-715](../server/routes/conversations.py#L711) | Idem — paridade | Mesma tolerância usando `previous` (já carregado na linha 720: `previous = dict(conv.get("custom_attributes") or {})`) | Baixo | S |
| 3 | Frontend contato só envia chaves definidas | [ContactInfoPanel.js:182](../web/static/js/components/contacts/ContactInfoPanel.js#L182) | Reenvia órfãos | Portar `buildConvAttrsPayload` do painel de conversa: iterar `customDefs`, `vazio→null` | Baixo | S |
| 4 | Limpeza dos órfãos em prod | endpoint existente | Rodar 1×/ambiente | `POST /api/custom-attributes/purge-orphans?applies_to=contact` (e `?applies_to=conversation` por garantia) — sem código novo | Baixo | S |
| 5 | Teste de regressão | [tests/test_endpoints.py:1252-1298](../tests/test_endpoints.py#L1252) | Não cobre "chave órfã armazenada não bloqueia save" | Adicionar caso: injetar `cw_id` no JSON, PUT `/info` com `{name, custom_attributes:{cw_id:...}}` → 200 + nome salvo | Baixo | S |

### Falsos positivos descartados

| Suspeita | Por que NÃO é o problema |
|----------|--------------------------|
| Permissão / RBAC | Usuário reproduziu com admin **e** atendente. O 400 vem da validação de atributo, não de `permission_denied` (que devolveria 403). Confirmado em [contacts.py:2237-2239](../server/routes/contacts.py#L2237): a checagem de permissão passa antes de chegar na validação |
| Painel de **conversa** também bugado | Não. `ConversationInfoPanel` já monta o payload só com `convDefs` ([:141](../web/static/js/components/contacts/ConversationInfoPanel.js#L141)) — órfãos nunca são enviados. O hardening no backend de conversa (item 2) é só defesa em profundidade |
| Falta o atributo `cpf` estar definido | `cpf` **está** definido (aparece nas definições `contact`). O campo CPF na UI funciona; quem quebra é `cw_id`/`cw_identifier` |
| Precisa de migration nova | Não. `purge_orphan_values` e o endpoint de purge já existem. Nenhum schema muda |

---

## 4. Fases / Roadmap

Esforço pequeno — três workstreams quase independentes. O frontend (B) sozinho já elimina o sintoma da UI; o backend (A) é a rede de segurança que cobre qualquer cliente/leftover futuro; a limpeza (C) higieniza a base. **A e B podem rodar em paralelo**; C depende de A estar deployado (o purge é seguro a qualquer momento, mas convém validar A antes para garantir que nada re-grava órfão).

```
WAVE 0   A (backend hardening) · B (frontend filtra)      ← paralelos, independentes
            │
            │ (após deploy validado)
WAVE 1   C (purge de dados em prod)                        ← operação, sem código
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|------|------|-----------|-------|-------|----------------|
| 0 | A | Backend tolera órfão armazenado (contato + conversa) + teste | 🟢 | Baixo | `tests/test_endpoints.py` verde incluindo o novo caso |
| 0 | B | Frontend `ContactInfoPanel` só envia chaves definidas | 🟢 | Baixo | Salvar nome de contato com `cw_id` armazenado → 200, nome persiste |
| 1 | C | Rodar purge de órfãos em cada ambiente | 🔴 `[depende de: A deployado]` | Baixo | `SELECT count(*) FILTER (WHERE custom_attributes ? 'cw_id')` = 0 |

---

### Fase A — Backend tolera chave órfã já armazenada

**Objetivo:** um `PUT` que reenvia uma chave sem definição, mas que **já existe no JSON armazenado** da entidade, não deve mais devolver 400 — a chave é deixada intacta (não gravada, não apagada, não validada).

**Itens:**
- `[sequencial]` Em [contacts.py:2242-2260](../server/routes/contacts.py#L2242): carregar o `custom_attributes` armazenado do contato up-front (via `ca_repo.get_values(contacts_table, contact.id)` ou reutilizando o contato já resolvido) **antes** do loop de validação. Na ramificação `definition is None`, além de `if key in known_keys: continue`, adicionar tolerância `if key in <chaves_armazenadas>: continue`. Só chave **nova E sem def** cai no `return _err(... não existe ...)` (preserva P50 — ver D3/P1).
  - ⚠️ Cuidado de ordenação: hoje `contact` só é resolvido dentro de `_update()`. Resolver o phone→id/attrs up-front exige uma leitura extra (aceitável) OU mover a validação para dentro do `_update` (maior refactor). Recomenda-se a leitura extra up-front para manter o 400 limpo antes de qualquer escrita.
- `[paralelo]` Em [conversations.py:711-724](../server/routes/conversations.py#L711): aplicar a mesma tolerância. Aqui é mais barato — `previous = dict(conv.get("custom_attributes") or {})` já existe (linha 720); basta **subir** essa leitura para antes do loop e tolerar `key in previous`.
- `[sequencial]` Teste em [tests/test_endpoints.py](../tests/test_endpoints.py) perto da linha 1295: pré-gravar um contato com `custom_attributes = {"cw_id": "123"}` (via repo/`set_values` direto ou UPDATE), então `PUT /info` com `{"name":"Novo","custom_attributes":{"cw_id":"123"}}` → esperar **200**, nome salvo, e o `cw_id` **preservado** no retorno. Adicionar caso simétrico para conversa se houver helper.

**Pronto quando:** `venv/bin/python -m pytest tests/test_endpoints.py -q` verde (contra Postgres de teste), incluindo o novo caso de chave órfã; um `PUT /info` com chave órfã armazenada devolve 200.

#### Status de execução — Fase A
**Estado:** ✅ Concluída
- **O que foi feito:** [contacts.py](../server/routes/contacts.py) `update_contact_info` — leitura up-front de `contact_repo.get_by_phone(phone)` → `stored_keys`; a ramificação `definition is None` agora tolera `key in known_keys or key in stored_keys`. [conversations.py](../server/routes/conversations.py) — `previous` subido para antes do loop; tolerância `key in known_keys or key in previous`. Teste de regressão em [tests/test_endpoints.py](../tests/test_endpoints.py) (injeta `cw_id`/`cw_identifier` via UPDATE, valida 200 + nome salvo + órfão preservado + P50 mantido para chave nova).
- **Como foi feito / decisões:** Reusei `get_by_phone` (retorna a row completa com `custom_attributes`) em vez de mover a validação pra dentro de `_update` — leitura extra barata, mantém o 400 limpo antes de qualquer escrita (conforme recomendação da própria Fase). Como `set_values` faz MERGE, a chave órfã tolerada (não entra em `valid_partial`) é preservada intacta.
- **Problemas / pendências:** Nenhum.
- **Verificação:** `venv/bin/python tests/test_endpoints.py` → **1591 passed, 0 failed**, incluindo os 4 novos checks do plano 77 (`new undefined key -> ok False (P50)`, `stored orphan re-sent -> 200`, `name saved`, `orphan key preserved`).

---

### Fase B — Frontend só envia chaves definidas (paridade com o painel de conversa)

**Objetivo:** o `ContactInfoPanel` deixa de reenviar chaves órfãs no save, espelhando o `buildConvAttrsPayload` do painel de conversa.

**Itens:**
- `[sequencial]` Em [ContactInfoPanel.js](../web/static/js/components/contacts/ContactInfoPanel.js): criar um builder (ex.: `buildCustomAttrsPayload()`) que itera **`customDefs`** e monta o payload `{ [def.attribute_key]: vazio? null : valor }` — exatamente como [ConversationInfoPanel.js:141-147](../web/static/js/components/contacts/ConversationInfoPanel.js#L141). No `handleSave` ([:182](../web/static/js/components/contacts/ContactInfoPanel.js#L182)), trocar `custom_attributes: customValues` por `custom_attributes: buildCustomAttrsPayload()`.
  - ⚠️ Manter a semântica de "limpar" (`vazio→null`), que o backend usa para remover a chave (`set_values` pop em `None`). Não usar `''` (rejeitado por tipos select/list na validação).
- `[sequencial]` Sanidade: como o render já só mostra `customDefs`, nenhum campo visível some. Os órfãos simplesmente param de ser enviados.

**Pronto quando:** com o backend antigo (sem Fase A) ou novo, abrir um contato que tenha `cw_id` no JSON, editar o nome e salvar → **sem erro**, nome persiste após reload. Nenhum atributo definido (ex.: CPF) é perdido.

#### Status de execução — Fase B
**Estado:** ✅ Concluída
- **O que foi feito:** [ContactInfoPanel.js](../web/static/js/components/contacts/ContactInfoPanel.js) — novo `buildCustomAttrsPayload()` (itera só `customDefs`, `vazio→null`), portado do `buildConvAttrsPayload` do painel de conversa. No `handleSave` o `custom_attributes: customValues` virou `custom_attributes: buildCustomAttrsPayload()`.
- **Como foi feito / decisões:** Paridade 1:1 com o painel de conversa (mesma semântica `vazio→null`, não `''`). Órfãos armazenados deixam de sair do cliente; o render já iterava só `customDefs`, então nenhum campo visível some.
- **Problemas / pendências:** Nenhum.
- **Verificação:** `node --check --input-type=module` no módulo → OK. Combinado com a Fase A, o backend também tolera órfão caso algum cliente antigo reenvie.

---

### Fase C — Limpeza dos órfãos em produção (e demais ambientes)

**Objetivo:** remover `cw_id`/`cw_identifier` (e qualquer outra chave sem def ativa) do JSON dos contatos, usando o mecanismo que já existe.

**Itens:**
- `[sequencial]` `[depende de: A deployado e validado]` Chamar `POST /api/custom-attributes/purge-orphans?applies_to=contact` no ambiente de produção (autenticado). Repetir com `?applies_to=conversation` por garantia.
  - Alternativa manual equivalente (se preferir SQL supervisionado): `UPDATE contacts SET custom_attributes = custom_attributes - 'cw_id' - 'cw_identifier' WHERE custom_attributes ?| array['cw_id','cw_identifier'];` — mas o endpoint é preferível (genérico, remove **toda** chave órfã, não só as duas conhecidas).
- `[sequencial]` Repetir no ambiente **dev** deste checkout se ele tiver contatos importados do Chatwoot.

**Pronto quando:** `SELECT count(*) FILTER (WHERE custom_attributes ? 'cw_id') AS n FROM contacts;` = **0** em prod; salvar contato continua funcionando (a rede de segurança da Fase A garante que, mesmo que sobre algum órfão, o save não quebra).

#### Status de execução — Fase C
**Estado:** ⬜ Não iniciada — operação de produção (depende de A deployado)
- **O que foi feito:** _(pendente — código de A/B pronto e verde; a purge é operacional)_
- **Como foi feito / decisões:** _(pendente)_
- **Problemas / pendências:** Executar após deploy de A: backup do banco → `POST /api/custom-attributes/purge-orphans?applies_to=contact` (e `?applies_to=conversation`) em prod. Nada de código.
- **Verificação:** _(pendente — `SELECT count(*) FILTER (WHERE custom_attributes ? 'cw_id') FROM contacts;` deve zerar)_

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Validação up-front no contato | Mover/duplicar a leitura do contato pode reordenar efeitos ou custar 1 query extra | Fazer leitura de `get_values` up-front (barata, indexada por PK); manter o 400 antes de qualquer escrita |
| Tolerância ampla demais no backend | Abrir 400→aceito para **qualquer** chave desconhecida quebraria P50 (typo de código passaria batido) | Tolerar **só** chave presente no JSON armazenado da própria entidade; chave nova e sem def continua 400 (ver P1) |
| `purge` apaga dado útil | `cw_id`/`cw_identifier` são só rastros do Chatwoot; sem uso na app | Confirmado: nenhuma def, nenhum código lê essas chaves (grep). Fazer backup do banco antes (rotina já existente em `~/whatsbot-backups`) |
| Postgres é o único backend | Operador JSON `?`/`-`/`?|` são específicos do Postgres | OK — o repo já é Postgres-only; `purge_orphan_values` faz o filtro em Python, portável |
| Regressão do painel de conversa | Alterar o backend de conversa poderia mudar o card de `attribute_set` | Só mexer na ramificação de erro (órfão → tolera); `changed`/`merged` continuam iterando `valid` (chaves definidas) |
| Modo escuro | Nenhuma tela nova | Sem impacto (só troca de payload no save existente) |

---

## 6. Perguntas em aberto

**P1 — Escopo da tolerância no backend.**
✅ DECIDIDO (2026-07-23): tolerar **apenas** chave órfã que já esteja no `custom_attributes` armazenado da própria entidade.
Contexto: o objetivo é destravar leftovers de migração sem enfraquecer a proteção P50 contra typo de chave vindo de código.
(a) Tolerar qualquer chave desconhecida (ignorar em vez de 400) — mais simples, mas perde P50.
(b) Tolerar só chave já armazenada — preserva P50 para chaves genuinamente novas. **Recomendado e escolhido (b).**

**P2 — Rodar o purge automaticamente no boot?**
⏸️ ADIADO: manter o purge como operação manual/endpoint. Um purge automático no startup misturaria limpeza de dados com boot e poderia surpreender. Reavaliar só se o problema reaparecer em massa.

---

## 7. Apêndice — arquivos-chave

**Backend**
- [server/routes/contacts.py](../server/routes/contacts.py) — `update_contact_info` (validação linha 2242-2260)
- [server/routes/conversations.py](../server/routes/conversations.py) — validação linha 702-724
- [db/repositories/custom_attribute_repo.py](../db/repositories/custom_attribute_repo.py) — `get_values`, `set_values`, `purge_orphan_values`
- [server/routes/custom_attributes.py](../server/routes/custom_attributes.py) — `POST /api/custom-attributes/purge-orphans` (linha 141)

**Frontend**
- [web/static/js/components/contacts/ContactInfoPanel.js](../web/static/js/components/contacts/ContactInfoPanel.js) — save do contato (alvo da Fase B)
- [web/static/js/components/contacts/ConversationInfoPanel.js](../web/static/js/components/contacts/ConversationInfoPanel.js) — `buildConvAttrsPayload` (padrão de referência, linha 141)

**Testes**
- [tests/test_endpoints.py](../tests/test_endpoints.py) — casos de `PUT /info` (linha 1252-1298)

---

## 8. Checklist de verificação

- [ ] `PUT /api/contacts/{phone}/info` com chave órfã armazenada (`cw_id`) → **200**, nome/email salvos, órfão preservado (Fase A)
- [ ] `PUT /api/atendimentos/{id}/attributes` (ou rota equivalente) com órfão armazenado → 200 (Fase A, paridade)
- [ ] Frontend: salvar nome de contato importado do Chatwoot → sem erro, persiste após reload; CPF e demais atributos definidos intactos (Fase B)
- [ ] `venv/bin/python -m pytest tests/test_endpoints.py -q` verde no Postgres de teste (`WHATSBOT_TEST_DB_URL`)
- [ ] Backup do banco antes do purge; `POST /api/custom-attributes/purge-orphans?applies_to=contact` executado (Fase C)
- [ ] `SELECT count(*) FILTER (WHERE custom_attributes ? 'cw_id') FROM contacts;` = 0 em prod (Fase C)
- [ ] Sem migration nova (nenhum schema alterado)
- [ ] Um refactor por commit; verde a cada fase
