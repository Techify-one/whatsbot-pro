# Plano 83 — Extrair os plugins do repositório do core

**Status:** PLANEJADO — nada executado. Escrito em 2026-07-25.
**Revisado em 2026-07-31** (auditoria de 25 agentes): todos os números medidos foram
re-medidos. **Nenhuma linha da matriz de paridade e nenhuma linha da matriz de testes
sobreviveu intacta.** As correções estão marcadas com 🔄 e a data.

**Objetivo do usuário:** o repositório `whatsbot-pro` (core) não deve mais carregar o
código-fonte dos plugins em `assets/plugin_examples/`. Cada plugin vira um `.zip`
distribuído pelo repo `whatsbot-pro-plugins`, o usuário instala por **Importar (.zip)**
e o plugin traz **tudo** junto — inclusive os próprios testes. No core fica só o `gowa`.

> 🔄 **Pré-requisito de ordem descoberto em 2026-07-31: o [plano 100](100-plano-devolver-ao-plugin-o-que-e-do-plugin.md) vem ANTES deste.**
> A pasta em `assets/plugin_examples/` é a **bancada** de todo movimento core→plugin: os 5
> arquivos do plano 84 nasceram lá, foram revisados por `git diff`, exercitados pela suíte e
> só então empacotados. Este plano remove a bancada. Na ordem invertida, cada extração
> passaria a exigir editar um `.zip` ou uma cópia gitignorada, sem diff revisável e sem teste.

---

## 1. Veredito

**É viável, e nenhum plugin tem acoplamento de RUNTIME que impeça.** O app sobe sem
qualquer uma das pastas: `plugins/bootstrap.py` só auto-instala `gowa`
(`BUNDLED_AUTO_INSTALL = ("gowa",)`, `bootstrap.py:37`).

> 🔄 **2026-07-31 — o veredito é verificado ESTATICAMENTE, não empiricamente.** Ninguém
> subiu o app sem as pastas. As duas âncoras que o plano citava não apontam mais para o que
> ele diz: `server/app.py:170-182` é o wiring de `channel_providers` (nenhum id de plugin) e
> `plugins/context.py:248-258` é a validação do `PLUGIN_ACTION_RE`. Os 3 hits de
> `assets/plugin_examples` fora de tests/assets/storages continuam sendo **comentários**.
> **Ação:** o gate da F0 passa a incluir um boot real com as pastas ausentes — é a única
> prova do veredito central deste plano.

O que quebra é (a) a suíte de testes e (b) a distribuição. E há **quatro pré-requisitos**
que precisam existir ANTES de apagar qualquer pasta — nenhum deles existe hoje.

🔄 **Baseline corrigido (2026-07-31).** No dia em que este plano foi escrito,
`assets/plugin_examples/` já tinha **7 pastas** — o `facebook_messenger` **já existia** e
ficou de fora da tabela, das fases e da matriz. Hoje são **8** (entrou `instagram`).

| Plugin | Veredito | Risco | Acoplamento que quebra |
|---|---|---|---|
| `protocolos` | removível com trabalho | baixo | 🔄 **1** (era "nenhum") — ver abaixo |
| `melhorias` | removível com trabalho | médio | 1 (só o `_copy_plugin`) |
| `website` | removível com trabalho | médio | 3 |
| `telegram` | removível com trabalho | alto | 5 |
| `whatsapp_cloud` | removível com trabalho | alto | 6 |
| 🔄 `facebook_messenger` | **fora do plano original** | alto | não publicado em lugar nenhum |
| 🔄 `instagram` | **fora do plano original** | alto | não publicado em lugar nenhum |

🔄 **`protocolos` não tem acoplamento "nenhum".** O core **hardcoda o path do plugin em duas
listas de rota SPA**: `server/app.py:542` inclui `/protocolos` e `/attendances` no
`_SPA_PATHS`, e `server/app.py:700-702` tem `@app.get("/protocolos")` com o comentário
*"path canônico da aba do plugin protocolos"*. Isso **não** é coberto por
`_PLUGIN_SPA_PATHS` (derivado de `screens[].path`), porque a única screen declarada pelo
plugin é `/protocolos/config` — a aba em si é um `overrideRoute('attendances')` do lado do
cliente. Sem o hardcode, um reload duro em `/protocolos` não seria servido.

🔄 **E há um acoplamento estrutural mais fundo:** **duas migrations do CORE são donas do
schema do `protocolos`**. A `0032` renomeia 6 tabelas `plugin_atendimentos_*` →
`plugin_protocolos_*` (mais colunas e índices) e a `0033` depende dela
(`down_revision = '0032_plugin_protocolos'`). A cadeia Alembic do core carrega, **para
sempre**, o vocabulário de um plugin que este plano quer expulsar. Deixar como fóssil
documentado é aceitável — mas tem de estar **escrito**, senão a próxima auditoria trata
como bug.

---

## 2. Os pré-requisitos (bloqueadores)

### P1 — Distribuição: os `.zip` não estão no git deste repo

```
$ git check-ignore -v assets/channel_plugins/telegram-plugin.zip
.gitignore:27:*-plugin.zip
$ git ls-files assets/channel_plugins/
assets/channel_plugins/README.md      ← só o README
```

🔄 **2026-07-31: continua idêntico, só que agora são 5 zips (entraram `facebook_messenger` e
`instagram`), somando 199 KB no disco e zero no git.** Não há script nem CI para gerá-los:
`scripts/` tem 3 arquivos e nenhum é de build, não existe `Makefile` e **não existe
`.github/`**. O `README.md` continua mandando rodar um heredoc à mão — hoje com 5 ids.

#### 🚨 P1 é bloqueador de DISPONIBILIDADE, não só de paridade (descoberto em 2026-07-31)

Ninguém mediu a consequência óbvia de os zips serem gitignorados: **um clone git não os
contém**, logo o build context do Docker (o Coolify clona o repo) **não os contém**, e o
container sobe com `assets/channel_plugins/` tendo só o `README.md`. `Dockerfile:37` faz
`COPY assets/ assets/` — copia o que existir no contexto, e num clone limpo não existe zip.

Nenhuma rota instala a partir de `assets/plugin_examples/` (o único consumidor é o
bootstrap, que só itera `gowa`). **Resultado medido: numa instalação nova em produção não
existe NENHUM caminho pela UI para instalar telegram/whatsapp_cloud/website/
facebook_messenger/instagram** — o operador precisa ter o `.zip` fora de banda, na máquina
dele.

Isso já é verdade hoje. Remover as pastas **piora** um problema que ninguém tinha enunciado.

#### 🔄 Estado do repo de plugins (medido em 2026-07-31)

O `Techify-one/whatsbot-pro-plugins` publica **16 plugins** (não 14), e os 3 rótulos de
versão (catalog.json, `<id>.json`, `plugin.yaml` dentro do zip) **batem nos 16** — a
inconsistência do commit `df71378` não se repetiu.

**Mas `instagram` e `facebook_messenger` NÃO estão publicados em lugar nenhum.** Para eles
o P1 ainda é bloqueador de **existência**, não de paridade.

#### 🔄 Matriz de paridade re-medida (2026-07-31)

| plugin | publicado | HEAD (git do core) | worktree (`assets/`) | instalado |
|---|---|---|---|---|
| `gowa` | **1.2.0** ⬅ atrás | 1.3.1 | 1.3.1 | 1.3.1 |
| `telegram` | **1.3.0** ⬅ atrás | 1.3.1 | 1.3.1 | 1.3.1 |
| `whatsapp_cloud` | 1.10.1 | 1.9.0 | 1.10.1 | 1.10.1 |
| `website` | **1.0.0** ⬅ atrás | 1.1.1 | 1.1.1 | 1.1.1 |
| `melhorias` | 1.7.0 | 1.7.0 | 1.7.0 | 1.7.0 |
| `protocolos` | **1.23.0** ⬅ atrás | 1.24.0 | 1.24.0 | 1.23.0 |
| `facebook_messenger` | **ausente** | 1.5.0 | 1.5.0 | — |
| `instagram` | **ausente** | 2.2.0 | 2.2.0 | 2.2.0 |

**O aviso de 25/07 ("as linhagens divergem nos DOIS sentidos") continua válido em espírito,
mas os exemplos concretos não descrevem mais o estado atual** — reusá-los induziria a uma
reconciliação errada. Hoje `assets/` ≥ publicado em 100% dos casos, e **nenhum byte
publicado está ausente de `assets/`**.

> 🔄 **Regra de método, aprendida na re-medição.** O `diff` cru mostra linhas presentes SÓ no
> publicado (`gowa/routes.py` 3 linhas, `website/channels.py` 4, `website/routes.py` 3).
> **Lendo os hunks**, são linhas **superadas**, não features perdidas (os decorators do gowa
> ganharam `core_permission`; o `post_create` do website ganhou `token_config_key`).
> **Paridade de plugin se decide LENDO os hunks — contagem de linhas só-de-um-lado não
> distingue "superado" de "perdido".**

> 🔄 **`gowa` não se instala por zip.** O publicado (1.2.0) está duas versões atrás do
> bundled (1.3.1). Como o `gowa` é o único plugin com upgrade automático no boot, um
> operador que importe a cópia publicada **rebaixa** o plugin — e o próximo boot desfaz
> sozinho. Vale uma regra explícita no catálogo.

**Ação:**
1. Reconciliar plugin a plugin, comparando **conteúdo**. 🔄 Hoje **4 dos 6** estão atrás na
   publicação (`gowa`, `telegram`, `website`, `protocolos`), mais 2 nunca publicados.
2. Criar `scripts/build_plugin_zips.py` no lugar do snippet manual. 🔄 Decidir se `gowa`,
   `melhorias` e `protocolos` entram (não têm zip local, mas **são** publicados).
3. 🔄 **NOVO — resolver a disponibilidade:** o build script não basta, o zip precisa **chegar
   ao servidor**. Três saídas: zip trackeado no git, uma rota "instalar bundled a partir de
   `assets/`", ou o catálogo remoto do §5.
4. Só então remover as pastas do core.

### P2 — "O plugin traz os próprios testes" só funciona pela metade

A **descoberta** funciona: `tests/conftest.py:162` varre `storages/plugins/<id>/tests/test_*.py`
— mas só quando o pytest roda "pelado" (`args_source == TESTPATHS`).

A **fixture que sobe o app não funciona**. `tests/support.py:73-80` (🔄 era 74-78):

```python
src = REAL_PLUGIN_EXAMPLES / plugin_id          # = assets/plugin_examples/<id>
if not src.is_dir():
    raise ValueError(f"build_test_app: unknown bundled plugin {plugin_id!r} ...")
```

Prova empírica reconfirmada em 2026-07-31: `melhorias: COPIADO OK` / `vendas_ia: FALHA`.

🔄 **Correções de escala:**
- O `vendas_ia` tem **67 testes** embutidos (3 arquivos), não 2 — e continua sendo o **único
  plugin do sistema com testes de verdade dentro de si**. Nenhuma pasta de `assets/` tem
  diretório `tests/`; `storages/plugins/whatsapp_cloud/tests/` existe e está **vazio**.
  O objetivo declarado no cabeçalho ("o plugin traz tudo junto — inclusive os próprios
  testes") tem hoje **1 caso de sucesso em 18 plugins**. Criar o `tests/` dentro de cada
  plugin extraído é item explícito de cada fase, não consequência automática da F0.1.
- Os `monkeypatch` de `REAL_PLUGIN_EXAMPLES` são **10 arquivos**, não 6 (+67% em 6 dias):
  `test_avaliacao_protocolo.py:32` (confirmado), `test_plano67_protocolos_toggle.py:29`,
  `test_plano68_reopen_assign_on_due.py:26`, `test_protocolos_popup.py:31`,
  `test_protocolos_relink_attr.py:31`, `test_utm_atendente.py:504-510`, e os quatro novos:
  `test_protocolos_ai_takeover.py:44`, `test_protocolos_atendente_provisorio.py:46`,
  `test_protocolos_mirror_sync.py:28`, `test_protocolos_skip_conditions.py:25`.
  **O número cresce a cada plano executado.**

#### 🚨 P2 tem um lado inverso que ninguém mediu

**112 testes do core dependem de conteúdo GITIGNORADO em `storages/plugins/`** —
`utm_atendente` 56, `retorno_automatico` 37, `vendas_ia` 10, `agendamento_retorno` 9. E
`tests/test_utm_atendente.py:56-59` faz `_load('utm')` em **nível de módulo, sem guard de
existência**: num clone limpo isso é **erro de COLETA**, não skip. Só
`test_retorno_automatico.py` e `test_vendas_ia_ad_store.py` degradam com segurança.

É **o mesmo bug de classe** do split `assets`↔`storages` que este plano usa como argumento
central da F1 — do outro lado. O `_copy_plugin` bidirecional não resolve isso sozinho.

**Ação (mudança no core, pequena):**
1. `_copy_plugin` procura em `assets/plugin_examples/<id>` **e** `storages/plugins/<id>`,
   nessa ordem, com erro citando os dois caminhos.
2. 🔄 **NOVO** — um helper único de "carregar módulo de plugin nos testes" (hoje
   reimplementado à mão em cada arquivo), que também procure nos dois lugares. Sem isso,
   extrair uma pasta quebra o arquivo de teste em **dois lugares independentes**: o
   `build_app` e o loader por caminho do próprio teste.
3. 🔄 **NOVO** — guard de skip obrigatório em todo teste do core que aponte para
   `storages/plugins/`.
4. 🔄 **NOVO** — helper de autenticação de teste **com teardown obrigatório**. Rotas de
   plugin gateadas por `core_permission` quebram **por ordem de execução**, porque o plano 48
   fecha a API assim que existe ≥1 usuário e o banco de teste é compartilhado pelo processo.
   O plano 84 topou nisso e resolveu com uma fixture que autentica como admin e **apaga o
   usuário no teardown**.

> 🔄 **Armadilha de falso-verde (plano 84).** O teste precisa patchar o módulo carregado pelo
> **loader** (`sys.modules['whatsbot_plugins.<id>.<mod>']`), que é um objeto **diferente** do
> carregado por caminho. Patchar o errado dá **teste verde e costura quebrada**.

### P3 — Testes do core que usam plugins como veículo

Não são testes do plugin: testam **comportamento do core** e só precisam de *um* provider
qualquer que responda.

> 🔄 **A tabela de 25/07 não é reproduzível — a metodologia nunca foi declarada.** Só a linha
> `website` sobreviveu. Recontagem de 2026-07-31, **com critério declarado** (funções
> `^(async )?def test_` por arquivo; um arquivo depende do plugin X se carrega a pasta,
> pacote ou rotas de X; classificação PLUGIN×CORE pelo sujeito-sob-teste do docstring):

| Plugin | testes que **vão pro plugin** | testes do **core** que precisam de substituto |
|---|---|---|
| `whatsapp_cloud` | 133 *(era 78)* | 66 *(era 61)* |
| `telegram` | — | ~46 |
| `website` | 16 | 6 ✅ *(única linha intacta)* |
| `melhorias` | 🔄 **57** *(era 40)* | 0 |
| `protocolos` | 255 | 0 |
| 🔄 `gowa` | — | **463 funções em 47 arquivos** — o maior de todos |
| 🔄 `facebook_messenger` | 38 em 2 arquivos | — |
| 🔄 `instagram` | 28 | — |
| 🔄 `utm_atendente` | 56 | — (plugin só em `storages/`) |
| 🔄 `retorno_automatico` | 37 | — |
| 🔄 `vendas_ia` | 10 | — |
| 🔄 `agendamento_retorno` | 9 | — |

🔄 O título "107 testes" não é reproduzível; use **"~90 funções de teste do core em 11
arquivos usam um plugin de canal como veículo"**. A conclusão **não muda** — se algo, o
volume subiu e a urgência também. `melhorias` triplicou em 6 dias (+39 funções em 4 commits),
o que confirma que **este plano acumula juros**.

**Ação:** um provider sintético residente em `tests/`.

> 🔄 **São 7 fakes de `Channel`, não 5 — e as 3 linhas citadas estavam todas erradas:**
> `_FakeChannel` (`test_endpoints.py:4919`, era 4869), `_FakeWindowed` (`:5020`, era 4944),
> `_FakeTplChannel` (`:6290`, era 6134), **`_P76GuardChannel` (`:7106` — omitido, e já
> existia em 25/07)**, `_CloudStub` (`test_source_id_per_channel.py:26` ✅),
> `_FakeChan` (`test_channel_identity_hooks.py:103` ✅) e **`_Meta`
> (`test_meta_graph_core.py:76`)**.
>
> 🔄 **E há um 8º dublê que a varredura por subclasse não pega:** `tests/fakes.py:34`
> `FakeGowaClient` (220 linhas). Não é um `Channel` — é o cliente GOWA falso, e é o que
> sustenta os testes que passam por `channels/registry.py:108` (`if provider == "gowa"`, que
> só constrói o canal se `gowa_client` existir). **Um `fake_provider.py` que o ignore não
> cobre o único provider que este plano mantém no core.**
>
> 🔄 **F0.2 pede um `kind` que não existe.** O dispatch trata **12** kinds (`message`,
> `reaction`, `receipt`, `edited`, `revoked`, `deleted`, `presence`, `group_participants`,
> `group_joined`, `call`, `newsletter`, `system`) — **não existe `kind="status"`**. O que o
> plano chamou de "status" é o `receipt`, que desde o plano 75 carrega
> `sent/delivered/read/failed/played`.
>
> 🔄 **A lista de carregamentos em nível de módulo estava incompleta e desatualizada.** Linhas
> atuais em `test_endpoints.py`: `:683` ✅, `:690` ✅, `:5129-5130` (era ~5052), `:5339` (era
> 5263), `:6595-6598` (era 6439), `:6863-6864` (eram 6681/6690), `:7091`. **Faltava o bloco
> `protocolos` inteiro** (`:2549-2820`, que lê de `storages/`) e ~18 call sites em 11 outros
> arquivos. **O raio de explosão de remover uma pasta é MAIOR do que o plano descrevia.**

### 🔄 P4 (NOVO) — A superfície de import não é versionada

Os plugins de `assets/` fazem **100+ imports de módulos internos do core** (45 de
`db.repositories`, 30 de `plugins.context`, 14 de `channels.base`, mais `server.routes.*`,
`server.background`, `server.authz`, `ai_engine`, `app.services`, `gowa.manager`,
`runtime.supervisor`, `db.tables`, `agent`) — **nenhum é API declarada**.

E **todos os 22 plugins** declaram o mesmo `whatsbot_api_version: ">=1.0,<2.0"` contra
`WHATSBOT_API_VERSION = "1.0.0"` (`plugins/semver.py:26`), que **nunca foi bumpada** desde a
criação do sistema de plugins. **O guard nunca rejeitou nada e, como está, nunca vai
rejeitar.**

Hoje o **único** detector de "refactor do core quebrou o plugin" é a fonte do plugin estar
no repo e a suíte exercitá-la. Este plano **remove esse detector sem substituto**, num repo
**sem CI** (não existe `.github/`) e sem check de versão remoto.

**Ação — uma das duas, obrigatória antes de F4/F5:**
- a suíte passa a rodar contra os **zips publicados** (contract test); **ou**
- define-se e versiona-se uma superfície pública (`plugins.context` + `channels.base`) e
  bumpa-se o `WHATSBOT_API_VERSION` de verdade.

---

## 3. ✅ O que a branch `plano-76` resolveu — FEITO

🔄 **2026-07-31: pré-requisito satisfeito, esta seção virou histórico.** A `plano-76` foi
mergeada em `developer` (`ea550b2`, `6b06dc4`); a branch remota não existe mais; o core já
recebeu correções **posteriores** em cima (plano 85 B1/B2).

Entregou: `services/providerCatalog.js` + `hooks/useProviderCatalog.js`; nomes de provider
hardcoded removidos de 7 componentes; e `WebhookHealthRow.js` movido **para fora do core**
via o slot genérico `channel.card.rows`.

**Some o aviso "sem ela, some um item de trabalho por plugin" — F3/F4/F5 encolhem um item
cada.**

---

## 4. Fases

### F0 — Pré-requisitos (bloqueia tudo)
1. `tests/support.py::_copy_plugin` procura em `assets/` **e** `storages/plugins/` (P2).
   Remover os 🔄 **10** `monkeypatch` que viram redundantes.
2. `tests/fake_provider.py` — consolida os 🔄 **7 fakes + o `FakeGowaClient`** num provider
   sintético completo: `provider_descriptor()`, `contact_type()`, `source_id_for()`, hooks de
   identidade, `media_limits`, `session_window_hours`, `capabilities` parametrizáveis, e
   `parse_inbound` capaz de emitir 🔄 `kind ∈ {message, system, receipt, reaction, edited}`.
3. `scripts/build_plugin_zips.py` — build reproduzível dos zips (P1).
4. 🔄 **Resolver a disponibilidade do zip no servidor** (P1 · ação 3).
5. Publicar os plugins no `whatsbot-pro-plugins` **antes** de remover (P1).
6. 🔄 Helper único de load de módulo de plugin + guards de skip + fixture de auth com
   teardown (P2 · ações 2-4).
7. 🔄 Decidir a saída do P4 (contract test **ou** superfície versionada).

**Gate:** suíte verde com o `_copy_plugin` novo, sem nenhuma pasta removida ainda —
🔄 **mais um boot real do app com as pastas ausentes** (§1).

### F1 — `protocolos`
🔄 Não é mais "custo ~zero", mas continua a fase mais barata. Após F0.1 nem o `monkeypatch`
é preciso.
1. Publicar `protocolos` 1.24.0.
2. 🔄 **Decidir o hardcode `/protocolos` + `/attendances`** em `server/app.py:542` e
   `:700-702`: fica como dívida declarada, ou vira screen declarada pelo plugin?
3. 🔄 **Declarar por escrito quem é dono das migrations `0032`/`0033`** do core (§1).
4. `git rm -r assets/plugin_examples/protocolos`.
5. Rodar `tests/test_endpoints.py` + os arquivos de protocolos.

> ⚠️ **Higiene:** o split assets↔storages do `protocolos` já causou um bug real e silencioso
> — `test_endpoints.py` abortava com `AttributeError: ... '_resolve_opener'` porque carregava
> de `storages/` (1.19.0) enquanto a função só existia em `assets/` (1.20.0). **746 de 1570
> checks (48%) e 31 seções nunca rodavam, com 0 FAIL aparente.** Ter UMA fonte por plugin
> elimina essa classe de bug — é o principal argumento técnico a favor deste plano.

### F2 — `melhorias`
🔄 **O volume triplicou: são 57 testes, não 18** (`test_melhorias_plugin.py` 11 + `test_p51_melhorias_gateway.py` 46),
com +39 funções em 6 dias por 4 commits de feature. Continuam todos testando o **plugin**.
Mais os testes JS que já moram no plugin. Os 14 restantes são core puro e não mudam.
1. Mover os 57 para `<plugin>/tests/`. 2. Publicar. 3. Remover a pasta.
🔄 **A fase precisa de data-alvo, não do rótulo "risco baixo"** — ela acumula juros.
🔄 ⚠️ `scripts/fake_ai_executor.py` (820 linhas, executor Claude Code falso que fala o
protocolo HMAC do gateway do plano 51) **fica órfão no core** quando `melhorias` sair.

### F3 — `website` ✅ números intactos
`test_website_widget.py` tem 23 testes: 16 do plugin, 6 do core (`build_app(["gowa","website"])`
nas linhas 257/269/277/290/298/311), 1 sem mudança. 🔄 **Reconfirmado linha a linha em
2026-07-31 — a única fase cujos números sobreviveram.** Pode ir para execução sem re-medição.

### F4 — `telegram`
Cuidado com os carregamentos em nível de módulo (lista corrigida no P3) — um erro aborta o
arquivo inteiro.

### F5 — `whatsapp_cloud` (o mais entrelaçado)
🔄 Ficou **maior e mais autônomo ao mesmo tempo**: o plano 84 acrescentou 4 arquivos + 1
migration (`alerts.py` 754 linhas, `filters.py` 104, `events.py` 20, `lifecycle.py` 51) e o
teste `test_plano84_account_alerts.py` (22 funções) — **e não tocou no core**.

Além dos call sites de nível de módulo, há `tests/manual_cloud_api_test.py:25` com
`from assets.plugin_examples.whatsapp_cloud.channels import ...` (o **único** import assim no
repo; some junto com a pasta).

Fica no core, e **está certo que fique** (valores duplicados, não import do plugin):
`channels/video_validate.py:36` `LEGACY_CLOUD_VIDEO_LIMITS` + `video_transcode.py:30` —
fallback retrocompat para canal `windowed` cujo plugin não declara `media_limits`.

### 🔄 F6 / F7 — `facebook_messenger` e `instagram` (NOVOS)
Fora do plano original. São os **únicos dois plugins de `assets/` que não estão publicados em
lugar nenhum** — para eles, P1 é bloqueador de existência. `facebook_messenger` tem 38
testes em 2 arquivos, `instagram` 28.

---

## 5. O custo que este plano NÃO elimina: atualização manual

Existe `POST /api/plugins/{id}/update` (🔄 `server/routes/plugins.py:437-529`, era 425-513) +
botão **"Atualizar"** (`PluginsManager.js:213-232`), que preserva tabelas `plugin_<id>_*`,
settings `plugin.<id>.*`, `plugin_migrations` e o flag `enabled`, roda migrations pendentes e
avisa em downgrade. `POST /api/plugins/import` recusa plugin já instalado.

**Não há catálogo remoto nem check de versão.** 🔄 Reconfirmado: grep por `catalog.json` /
`whatsbot-pro-plugins` / `marketplace` nos `.py`/`.js` do core → **zero hits**. O único link é
estático para a Loja *community* (`PluginsManager.js:270`), que é outro repo.

Contraste com o `gowa`: `plugins/bootstrap.py:127-173` compara semver e substitui
`storages/plugins/gowa` no boot — automático a cada `git pull`/redeploy.

> 🔄 **O trade-off de 25/07 estava ERRADO e precisa ser reescrito.** A premissa *"atualiza
> sozinho no deploy"* vale **só para o `gowa`** — e o `gowa` não está em nenhuma fase deste
> plano. `telegram`, `whatsapp_cloud`, `website`, `facebook_messenger` e `instagram` são
> import-only desde o plano 33: o `git pull` entrega a **fonte** em `assets/`, e **nada copia
> de lá para `storages/`**.
>
> **Prova em produção:** a instância Redes Brasil roda um core cujo `assets/` tem telegram
> 1.3.1 / website 1.1.1 / whatsapp_cloud 1.10.1, enquanto a **instalação** roda 1.2.2 / 1.0.1
> / 1.9.0.
>
> **Para os canais que este plano quer extrair (F3-F7), a extração não regride nada — o custo
> manual já é 100% da realidade.** Quem regrediria de verdade seria o `gowa`, que o plano não
> propõe extrair.
>
> **O argumento correto para adiar F4/F5 não é "perde o update automático"** (não existe para
> esses providers), e sim: **a fonte deixa de viajar com o core**, então some a garantia de
> que o zip publicado bate com a versão do core que a instância roda — mais o acoplamento de
> teste (P3) e o risco do P4.

> 🔄 **A mitigação sugerida tem um pré-requisito.** O `catalog.json` já existe, e **hoje seria
> uma fonte ruim justamente para canais**: lista `gowa 1.2.0` (core e produção em 1.3.1),
> `telegram 1.3.0` (core 1.3.1), `website 1.0.0` (core 1.1.1) e **não lista
> `facebook_messenger` nem `instagram`**. Um check ingênuo diria *"gowa desatualizado"* para
> uma instância que está na versão MAIS NOVA, e o "update" seria um **downgrade que o próximo
> boot desfaz sozinho**. **O catálogo só vira fonte de verdade DEPOIS que o canal sair do
> core** — enquanto os dois coexistirem, o check tem de ignorar plugin bundled.

> 🔄 **Contrapartida positiva (plano 84):** quanto mais zero-core o plugin, mais o zip vira
> unidade de deploy autônoma — que é exatamente a premissa deste plano. A F8 original do 84
> exigia "core antes do zip"; com a reimplementação sem core o item foi riscado. **Critério de
> aceite por fase: "o plugin extraído instala e funciona num core da release anterior".**

---

## 6. Resumo da recomendação

| Fase | Recomendação |
|---|---|
| 🔄 **Plano 100 antes de tudo** | A bancada core→plugin é a pasta que este plano remove |
| F0 | Fazer. Vale por si só (F0.1 elimina a classe de bug do split assets↔storages) |
| F1 `protocolos` | Fazer junto com F0. 🔄 Ganhou 2 decisões (SPA hardcode, dono das migrations) |
| F2 `melhorias` | Fazer, **com data-alvo** — 🔄 os testes triplicaram em 6 dias |
| F3 `website` | Fazer depois de F0.2. ✅ Números reconfirmados, pronto para executar |
| F4/F5 `telegram`/`whatsapp_cloud` | **Adiar** — 🔄 agora por **P4** (superfície de import não versionada), motivo mais forte que "atualização manual" |
| 🔄 F6/F7 `facebook_messenger`/`instagram` | **Adiar** — nem publicados estão; P1 é bloqueador de existência |

---

## 7. 🔄 Áreas correlatas que a auditoria encontrou (não bloqueiam, mas contaminam)

- **`custom_sounds` foi absorvido na direção CONTRÁRIA** (plugin → core): `server/sound_catalog.py`
  (180 linhas), `server/routes/sound_prefs.py` (212), `db/repositories/custom_sound_repo.py`,
  a tabela `custom_sounds` e permissões na migration `0062` — **enquanto o CLAUDE.md ainda o
  descreve como plugin da Loja community**. Qualquer plano de fronteira core/plugin precisa
  reconciliar essa contradição.
- **Migrations com provider hardcoded:** `0047_source_id_native.py` tem 3 cláusulas
  `c.provider <> 'gowa'` e `0050_contact_type.py` escreve `contact_type='telegram'` filtrando
  por `ch.provider='telegram'`. São **fósseis legítimos** (migration é histórico imutável) —
  mas precisam estar declarados como exceção, senão a próxima auditoria de `if provider ==`
  os reporta como violação.
- **Não existe `package.json`**: os testes JS (`node --test`) não têm runner nem manifesto e
  são invocados à mão. Os 5 `.test.js` que já moram dentro de plugins **não rodam em lugar
  nenhum automaticamente**. O P2 trata só do lado Python; o lado JS de "o plugin traz seus
  testes" já existe e já é inexecutável.
