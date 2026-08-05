# Plano 103 — Canal GOWA novo nasce com "Grupo / Comunidade" DESMARCADO

> **Status:** ✅ EXECUTADO (2026-08-05) — F1/F2/F3 concluídas; falta só a conferência na tela pelo usuário (§8) · **Escopo:** pequeno (1 linha de produto + testes + doc)
> **Origem:** pedido do usuário — *"ninguém vai se lembrar de desmarcar isso na hora de criar uma caixa"*. Nasceu do incidente do plano 102 (118 contatos de grupo criados por um canal novo que veio com grupos ligados).
> **Método:** leitura do código com `arquivo:linha` verificado + rastreio do valor do descriptor até o formulário e até o runtime.
> Trocar o **default de criação** do multiselect "O que deve aparecer no painel" para `person` + `person_lid`, sem tocar no **fallback de runtime** — que é outro valor, em outro arquivo, com outro efeito (retroativo).
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| D1 ✅ (2026-08-05) | Canal GOWA **novo** deve nascer sem grupo marcado | Muda o `default` do descriptor, não o catálogo de opções (o usuário continua podendo marcar) |
| D2 ✅ (2026-08-05) | Canal **existente não muda de comportamento** — ninguém acorda com grupos calados | ⛔ `channels/jid.py` `DEFAULT_ALLOWED_JID_TYPES` fica **intocado** (§2) |
| D3 ✅ (2026-08-05) | A opção continua existindo e visível na criação | Nada de esconder o checkbox nem mover para "avançado" |

---

## 1. Resumo executivo

Hoje o formulário de criação de canal GOWA já vem com **três** tipos marcados: pessoa, pessoa em modo privacidade e **grupo**. Quem cria um número para disparo/atendimento individual não repara, salva, e o painel passa a materializar todo grupo de que o número participa — foi exatamente o que gerou os 118 contatos do plano 102.

A correção é de **uma linha**: `GOWA_DEFAULT_JID_TYPES` em [gowa_channel.py:55](../channels/providers/gowa_channel.py#L55) passa de `["person","person_lid","group"]` para `["person","person_lid"]`. O formulário é genérico e lê o `default` do descriptor ([constants.js:144-153](../web/static/js/components/channels/constants.js#L144-L153)), então o frontend não muda.

O cuidado real está em **não confundir esse valor com o outro** default, o de runtime ([jid.py:31](../channels/jid.py#L31) — ver §2), que decide o que fazer quando um canal **não tem** a chave salva. Mexer nele silenciaria grupos em canais legados — o oposto da D2.

---

## 2. Como funciona hoje (mapa verificado) — os DOIS defaults

⚠️ **Este é o ponto que o executor não pode errar.** Existem dois valores parecidos, em arquivos diferentes, com efeitos opostos:

| | **Default de CRIAÇÃO** (este plano muda) | **Fallback de RUNTIME** (não mexer — D2) |
|---|---|---|
| Constante | `GOWA_DEFAULT_JID_TYPES` | `DEFAULT_ALLOWED_JID_TYPES` |
| Onde | [gowa_channel.py:55](../channels/providers/gowa_channel.py#L55) | [jid.py:31](../channels/jid.py#L31) |
| Quem lê | `provider_descriptor()` → `config_fields[].default` ([gowa_channel.py:117-123](../channels/providers/gowa_channel.py#L117-L123)) | `normalize_allowed_types()` ([jid.py:69-82](../channels/jid.py#L69-L82)) |
| Quando vale | Ao **montar o formulário** de canal novo | Quando o canal **não tem** `config.allowed_jid_types` (ou tem lixo) |
| Alcance | Só canal criado dali para frente | **Todo canal legado** que nunca salvou a chave |
| Mudar isso… | …faz canal novo nascer sem grupo ✅ | …**cala grupos retroativamente** em canais antigos ⛔ |

**Cadeia verificada do valor até a tela:**

| # | Passo | Onde |
|---|---|---|
| 1 | O provider declara o catálogo + o default | [gowa_channel.py:41-55](../channels/providers/gowa_channel.py#L41-L55) |
| 2 | O descriptor expõe `{"type":"multiselect","options":GOWA_JID_TYPES,"default":list(GOWA_DEFAULT_JID_TYPES)}` | [gowa_channel.py:117-123](../channels/providers/gowa_channel.py#L117-L123) |
| 3 | `GET /api/channels/providers` devolve o descriptor | [channel_service.py:474-490](../app/services/channel_service.py#L474-L490) |
| 4 | O formulário de criação semeia o estado a partir do `default` | [constants.js:144-153](../web/static/js/components/channels/constants.js#L144-L153) (`initialConfigValues`, ramo `multiselect`) |
| 5 | O `MultiSelect` genérico renderiza — sem saber o que é um JID | [DescriptorFields.js:41-63](../web/static/js/components/channels/DescriptorFields.js#L41-L63) |
| 6 | `buildCreatePayload` manda os `configValues` no POST ⇒ a chave é **sempre gravada** quando se cria pela UI | [constants.js:164+](../web/static/js/components/channels/constants.js#L164) |
| 7 | No inbound, o filtro lê a chave do canal (cache 30 s) e aplica | [message_ingest_service.py:125-146](../app/services/message_ingest_service.py#L125-L146) + [:359-374](../app/services/message_ingest_service.py#L359-L374) |

**Consequência boa do passo 6:** como a UI sempre grava a chave, o fallback de runtime nunca entra em jogo para canal criado pela tela. Por isso mudar só o passo 1 resolve o pedido inteiro.

⚠️ **Efeito colateral a decidir (P1):** [ChannelEditForm.js:28-42](../web/static/js/components/channels/ChannelEditForm.js#L28-L42) semeia o multiselect com `cfg[f.key]` **e cai no `f.default` quando o array salvo está ausente OU vazio**. Então, ao abrir a edição de um canal legado que nunca salvou a chave, a tela passará a exibir "grupo desmarcado" enquanto o runtime ainda aceita grupo — divergência visual até o primeiro save. Não é regressão nova (o mesmo já acontece hoje com qualquer campo default), mas fica mais visível.

---

## 3. Inventário da mudança

| # | Item | Onde | O que falta | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|---|
| 1 | Default de criação | [gowa_channel.py:55](../channels/providers/gowa_channel.py#L55) | remover `"group"` da lista | trocar a constante; deixar comentário explicando que é o default **de criação**, distinto do de runtime | baixo | **S** |
| 2 | Fallback de runtime | [jid.py:31](../channels/jid.py#L31) | **nada** — permanece com `group` | acrescentar comentário curto contrastando com o #1, para o próximo leitor não "consertar" por engano | baixo | **S** |
| 3 | Documentação | `CLAUDE.md`, seção "Filtro de tipos de JID" | a linha diz `**Default**: person, person_lid, group` sem distinguir os dois casos | reescrever para "canal novo nasce com `person`+`person_lid`; canal sem a chave salva cai em `person`+`person_lid`+`group`" | baixo | **S** |
| 4 | Teste de contrato | a criar (sugestão: `tests/integration/`) | não há teste travando o default do descriptor | asserção dupla: descriptor **sem** `group` **e** `jid.DEFAULT_ALLOWED_JID_TYPES` **com** `group` — é o par que impede a regressão nos dois sentidos | baixo | **S** |
| 5 | Testes existentes | [test_webhook_characterization.py:196-205](../tests/integration/characterization/test_webhook_characterization.py#L196-L205), [legacy_endpoints.py:4828+](../tests/core/legacy/legacy_endpoints.py#L4828) | verificar se algum assert/docstring depende do default do descriptor | o docstring de caracterização fala do default de **runtime** (inalterado) — provavelmente só revisar o texto; `legacy_endpoints.py:4878` passa config explícita, não deve quebrar | baixo | **S** |
| 6 | Front-end | [constants.js](../web/static/js/components/channels/constants.js), [DescriptorFields.js](../web/static/js/components/channels/DescriptorFields.js) | **nada** | o formulário é dirigido pelo descriptor (plano 33) — zero mudança | — | — |

### Falsos positivos descartados

| Suspeita | Por que NÃO precisa mexer |
|---|---|
| "Tem que mudar o frontend para desmarcar" | `initialConfigValues` já semeia do `default` do descriptor ([constants.js:147](../web/static/js/components/channels/constants.js#L147)); o core não conhece "JID type" |
| "Tem que mudar `channels/jid.py` também" | É o fallback de runtime — mudar viola a D2 (silenciaria grupo em canal legado sem a chave) |
| "Precisa de migration para os canais existentes" | O pedido é só sobre criação; canal existente mantém o que salvou |
| "Os outros providers precisam do mesmo ajuste" | `allowed_jid_types` é exclusivo do GOWA (o sufixo de JID é conceito do WhatsApp); Telegram/Cloud/website não têm o campo |
| "Precisa mudar a rota de criação para forçar o default" | A UI sempre envia a chave (§2 passo 6). Só um cliente de API que omita o campo cairia no fallback — ver P1 |

---

## 4. Fases / Roadmap

```
WAVE 0   F1 (default + comentários)  ·  F2 (doc)      🟢 independentes
              │
              └── [bloqueia: F3]
WAVE 1   F3 (testes)                                   🔴 [depende de: F1]
```

| Wave | Fase | Workstream | Paraleliza? | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F1** | Backend — constante + comentários | 🟢 | baixo | canal novo abre sem grupo marcado |
| 0 | **F2** | Documentação (`CLAUDE.md`) | 🟢 | baixo | a seção distingue os dois defaults |
| 1 | **F3** | Testes (novo + revisão dos existentes) | 🔴 sozinha | baixo | suíte verde no Postgres |

---

### F1 — Trocar o default de criação (🟢)

**Objetivo:** canal GOWA novo nasce sem "Grupo / Comunidade" marcado.

**Itens:**
1. `[sequencial]` [gowa_channel.py:55](../channels/providers/gowa_channel.py#L55): `GOWA_DEFAULT_JID_TYPES = ["person", "person_lid"]`.
2. `[paralelo]` No mesmo ponto, comentário curto: *"default de CRIAÇÃO (semeia o formulário). NÃO confundir com `jid.DEFAULT_ALLOWED_JID_TYPES`, o fallback de runtime de canal sem a chave salva — mudar aquele é retroativo."*
3. `[paralelo]` [jid.py:31](../channels/jid.py#L31): comentário espelho apontando para o #1.
4. `[paralelo]` Opcional (melhora a tela sem lógica nova): ajustar o `help` do campo em [gowa_channel.py:121-123](../channels/providers/gowa_channel.py#L121-L123) para avisar que marcar "Grupo / Comunidade" faz **todo** grupo do número virar conversa no painel.

**Pronto quando:** subir o servidor, abrir **Canais → Novo canal → GOWA** e ver "Pessoa (contato)" e "Pessoa (modo privacidade)" marcados e **"Grupo / Comunidade" desmarcado**; criar o canal e conferir no banco que `config.allowed_jid_types` gravou `["person","person_lid"]`.

#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-08-05)
- **O que foi feito:**
  - [channels/providers/gowa_channel.py](../channels/providers/gowa_channel.py): `GOWA_DEFAULT_JID_TYPES = ["person", "person_lid"]` (item 1) + comentário de bloco marcando-o como default de **criação** e apontando o contraste com o de runtime (item 2).
  - [channels/jid.py](../channels/jid.py): comentário espelho em `DEFAULT_ALLOWED_JID_TYPES` — marca que é o fallback de **runtime**, que canal novo nasce do outro, e que tirar `GROUP` dali é retroativo (item 3). **Valor intocado** (D2).
  - [channels/providers/gowa_channel.py](../channels/providers/gowa_channel.py) `help` do campo `allowed_jid_types`: acrescentado o aviso de que marcar "Grupo / Comunidade" faz TODO grupo do número virar conversa (item 4, opcional).
- **Como foi feito / decisões:**
  - **P1 decidida: (b)** — a rota de criação **não** semeia defaults do descriptor para chaves omitidas. O pedido é sobre a tela, a UI sempre envia a chave (§2 passo 6), e (a) mexeria num caminho compartilhado por todos os providers sem consumidor pedindo.
  - Comentários em **inglês** nas duas pontas (convenção do `CLAUDE.md`: comentário em inglês, texto de tela em PT-BR) — a 1ª versão saiu em PT-BR e foi reescrita para não destoar do entorno. O `help` do campo, por ser texto de tela, é PT-BR.
- **Problemas / pendências:** nenhuma. Frontend não mudou (`initialConfigValues` já semeia do `default` do descriptor).
- **Verificação:** teste novo da F3 trava as duas pontas (descriptor sem `group` × runtime com `group`); suíte de integração/contratos e `node --test` verdes — ver F3. Validação na tela pendente do usuário (o servidor dev não foi reiniciado neste passo).

---

### F2 — Documentação (🟢)

**Objetivo:** o `CLAUDE.md` deixar de dizer que "o default é person/person_lid/group" sem qualificar qual dos dois.

**Itens:**
1. `[sequencial]` `CLAUDE.md`, seção **"Filtro de tipos de JID (canal GOWA)"**: substituir a frase de default por duas — canal **novo** nasce com `person` + `person_lid`; canal **sem a chave salva** (legado) continua caindo em `person` + `person_lid` + `group`.
2. `[paralelo]` Citar o motivo em uma linha (incidente dos 118 contatos, plano 102), para o próximo leitor não reverter achando que é bug.

**Pronto quando:** a seção descreve os dois valores e aponta `arquivo:linha` de cada um.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-08-05)
- **O que foi feito:** `CLAUDE.md`, seção "Filtro de tipos de JID (canal GOWA)": a frase única "**Default**: `person`, `person_lid`, `group`" saiu e no lugar entrou uma tabela de 4 linhas contrastando **criação** × **runtime** (constante, `arquivo:linha`, valor, quando vale, alcance), mais um parágrafo dizendo que canal novo nasce sem grupo, o porquê (os 118 contatos do plano 102) e que a opção continua a um clique (item 2).
- **Como foi feito / decisões:** tabela em vez de duas frases — os dois valores só param de ser confundidos quando ficam lado a lado com o *alcance* explícito ("só canal novo" × "todo canal legado"); é a mesma tabela do §2 deste plano, agora no lugar que o próximo leitor abre primeiro.
- **Problemas / pendências:** nenhuma.
- **Verificação:** seção relida; os dois `arquivo:linha` conferidos contra o código pós-F1 — os comentários da F1 empurraram as constantes, então os links foram corrigidos para `gowa_channel.py:61` e `jid.py:38` (as referências do próprio plano, escritas antes da F1, ainda citam 55/32).

---

### F3 — Testes (🔴 sozinha) [depende de: F1]

**Objetivo:** travar os dois lados para que ninguém "conserte" o default errado depois.

**Itens:**
1. `[sequencial]` Teste novo (sugestão `tests/integration/test_gowa_jid_defaults.py`) com **duas asserções no mesmo arquivo**:
   - o `provider_descriptor()` do GOWA traz `allowed_jid_types.default == ["person","person_lid"]` (sem `group`);
   - `channels.jid.DEFAULT_ALLOWED_JID_TYPES` **contém** `group` (o fallback de runtime não mudou).
   O par é o ponto: separados, cada um convida a alterar o outro.
2. `[paralelo]` Se der pouco trabalho, um teste de ponta a ponta: `POST /api/channels` com o payload que a UI monta ⇒ a row gravada tem `config.allowed_jid_types` sem `group`.
3. `[paralelo]` Revisar o docstring de [test_webhook_characterization.py:200](../tests/integration/characterization/test_webhook_characterization.py#L200) ("default = person/person_lid/group") — ele descreve o **runtime**, que não mudou; ajustar a redação só se ficar ambíguo.
4. `[sequencial]` Rodar a suíte: `venv/bin/python -m pytest tests/integration tests/contracts` com `WHATSBOT_TEST_DB_URL` apontando para um banco de teste.

**Pronto quando:** o teste novo passa, a suíte de integração/contratos fica verde, e nenhuma caracterização quebrou.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída (2026-08-05)
- **O que foi feito:**
  - Criado [tests/integration/test_gowa_jid_defaults.py](../tests/integration/test_gowa_jid_defaults.py) com **5 testes**: (a) descriptor com `default == ["person","person_lid"]`; (b) `group` continua **existindo como opção** (D3) e todo valor do catálogo é um tipo conhecido do runtime; (c) `jid.DEFAULT_ALLOWED_JID_TYPES` ainda contém `group`, inclusive via `normalize_allowed_types(None)`/`is_allowed`; (d) a asserção que **amarra o par** — os dois valores têm de continuar DIFERENTES, então alinhá-los derruba o teste independente de qual arquivo foi "consertado"; (e) ponta a ponta (item 2): `GET /api/channels/providers` → `POST /api/channels` com os `configValues` que a UI monta ⇒ a row gravada tem `allowed_jid_types` sem `group`.
  - Revisado o docstring de [test_webhook_characterization.py:201](../tests/integration/characterization/test_webhook_characterization.py#L201) (item 3): "default = person/person_lid/group" virou "runtime fallback ... NOT the create-time descriptor default, which drops `group`" — o texto tinha ficado ambíguo depois da F1.
- **Como foi feito / decisões:**
  - Os dois lados ficaram **no mesmo arquivo**, como o plano pediu: separados, cada um convidaria a alterar o outro.
  - O teste (b) foi além do plano de propósito — travar só o `default` deixaria passar um "conserto" que removesse `group` do **catálogo** (`GOWA_JID_TYPES`), o que violaria a D3 sem quebrar nada.
- **Problemas / pendências:**
  - **1 falha PRÉ-EXISTENTE, não relacionada**: `tests/integration/characterization/test_audit_characterization.py::test_audit_matrix_is_complete` (`AUDITABLE_EVENTS drifted from the characterization matrix; missing={channel.created, channel.updated, channel.deleted, channel.restored, channel.members_changed, channel.session_action, channel.duplicate_refused, plugin.imported, plugin.deleted}`). Confirmada como anterior a este plano: `git stash` das 3 mudanças de código e a falha se repete **idêntica**. É a matriz de auditoria desatualizada em relação aos eventos de canal/plugin já existentes — nada a ver com JID types.
- **Verificação:**
  - `venv/bin/python -m pytest tests/integration/test_gowa_jid_defaults.py -q` ⇒ **5 passed**.
  - `venv/bin/python -m pytest tests/integration/test_gowa_jid_defaults.py tests/integration/characterization/test_webhook_characterization.py -q` ⇒ **33 passed** (as caracterizações de `group`/`newsletter`/`broadcast` seguem verdes — o canal `default` da suíte não grava a chave, então continua no fallback de runtime, provando a D2 na prática).
  - `venv/bin/python -m pytest tests/integration tests/contracts -q` (Postgres via `WHATSBOT_TEST_DB_URL`) ⇒ **736 passed · 1 failed · 2 skipped** (739 no total). O único vermelho é a falha pré-existente acima; os 2 skips são conhecidos (`test_execution_characterization.py:339` e `test_lifecycle_characterization.py:437`).
  - `node --test web/static/js/components/channels/constants.test.js` ⇒ **19/19**. (⚠️ passar o **diretório** para o `node --test` falha nesta versão do Node por resolver o path como módulo — não é falha de teste.)

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Confundir os dois defaults | Mexer em `jid.py:31` cala grupos em canal legado sem a chave — regressão silenciosa, só percebida quando alguém sentir falta de uma conversa | Tabela do §2 + comentários espelho (F1 itens 2-3) + teste pareado (F3 item 1) |
| Formulário de **edição** | Canal legado sem a chave salva passa a **exibir** grupo desmarcado enquanto o runtime ainda aceita ([ChannelEditForm.js:33-36](../web/static/js/components/channels/ChannelEditForm.js#L33-L36)) | Divergência só visual e resolvida no primeiro save; ver P1 se incomodar |
| Canal criado por API | Cliente que omitir `allowed_jid_types` cai no fallback de runtime (com grupo) | Ver P1(b); a UI sempre envia a chave |
| Expectativa de quem usa grupo | Alguém que crie canal contando com grupo ligado vai estranhar | A opção continua visível e a um clique (D3); o `help` (F1 item 4) explica |
| Cache de 30 s | Editar o canal e testar em seguida pode ler o valor antigo | O `PUT /api/channels/{id}` invalida o cache ([message_ingest_service.py:141-146](../app/services/message_ingest_service.py#L141-L146)); ao testar por SQL, esperar 30 s |

---

## 6. Perguntas em aberto

**P1 — A rota de criação deve gravar o default do descriptor quando o cliente omitir o campo?**
✅ **DECIDIDO na F1 (2026-08-05): (b) — não.** O `POST /api/channels` segue sem semear defaults do descriptor; canal criado por API que omita `allowed_jid_types` continua caindo no fallback de runtime (com `group`). Motivo: o pedido é sobre a tela, a UI sempre envia a chave, e (a) acrescentaria lógica a um caminho compartilhado por todos os providers sem consumidor pedindo. Reabrir se aparecer canal criado por automação.
Contexto: a UI sempre envia (§2 passo 6), então isso só afeta canal criado por API/script.
(a) Sim — `POST /api/channels` semeia `config_fields[].default` para toda chave ausente. Genérico (serve a qualquer provider), fecha o buraco de vez, mas acrescenta lógica ao core.
(b) Não — mantém o fallback de runtime como está e aceita que canal criado por API herde `group`.
**Recomendação:** (b) por ora — o pedido do usuário é sobre a tela, e (a) mexe num caminho compartilhado por todos os providers sem consumidor pedindo. Reabrir se aparecer canal criado por automação.

**P2 — Aproveitar para desmarcar `group` no `Equipe_01` de produção?**
⏸️ **REMETIDO ao plano 102 (F1/P1).** Este plano só muda o default de canais **novos**; canal existente é operação, não código.

---

## 7. Apêndice — arquivos-chave

| Camada | Arquivo | Papel |
|---|---|---|
| Backend (provider) | [channels/providers/gowa_channel.py:41-55](../channels/providers/gowa_channel.py#L41-L55), [:117-123](../channels/providers/gowa_channel.py#L117-L123) | **edita** — catálogo + default de criação + `help` |
| Backend (core) | [channels/jid.py:31](../channels/jid.py#L31) | **só comentário** — fallback de runtime, não mexer no valor |
| Backend (serviço) | [app/services/channel_service.py:474-490](../app/services/channel_service.py#L474-L490) | leitura — publica o descriptor, não muda |
| Frontend | [web/static/js/components/channels/constants.js:144-153](../web/static/js/components/channels/constants.js#L144-L153), [DescriptorFields.js:41-63](../web/static/js/components/channels/DescriptorFields.js#L41-L63) | leitura — genérico, não muda |
| Frontend (edição) | [web/static/js/components/channels/ChannelEditForm.js:28-42](../web/static/js/components/channels/ChannelEditForm.js#L28-L42) | leitura — origem do efeito visual do §5 |
| Testes | `tests/integration/test_gowa_jid_defaults.py` (novo), [test_webhook_characterization.py:196-205](../tests/integration/characterization/test_webhook_characterization.py#L196-L205) | trava a mudança |
| Doc | `CLAUDE.md` → "Filtro de tipos de JID (canal GOWA)" | distinguir os dois defaults |

---

## 8. Checklist de verificação

- [ ] ⏳ **PENDENTE (usuário)** — Canal GOWA novo abre com "Grupo / Comunidade" **desmarcado** e os dois de pessoa marcados *(exige subir o servidor dev; o descriptor servido pela API já foi conferido por teste)*
- [x] Criar canal pela UI grava `config.allowed_jid_types = ["person","person_lid"]` — coberto pelo teste ponta a ponta `test_create_channel_persists_default_without_group`
- [x] `channels/jid.py` `DEFAULT_ALLOWED_JID_TYPES` **inalterado** (ainda contém `group`) — só ganhou comentário
- [x] Canal GOWA **existente** que já tinha grupo marcado continua recebendo grupo — nada lê o default de criação em runtime; as caracterizações de grupo do webhook seguem verdes
- [x] Teste novo (par descriptor × runtime) passa — 5 testes
- [x] `venv/bin/python -m pytest tests/integration tests/contracts` no Postgres — **736 passed / 1 failed / 2 skipped**; o único vermelho é `test_audit_matrix_is_complete`, falha **pré-existente** comprovada por `git stash` (ver F3)
- [x] `node --test` nos módulos puros de canais (`constants.test.js`) verde — 19/19
- [x] `CLAUDE.md` atualizado distinguindo default de criação × fallback de runtime
- [x] Modo escuro: nada novo na tela (zero mudança de frontend — só o texto do `help` e a lista semeada mudaram)
