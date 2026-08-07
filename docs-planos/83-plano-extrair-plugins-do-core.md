# Plano 83 — Extrair os plugins do repositório do core

**Status:** EM EXECUÇÃO — fundações da F0 entregues e publicação externa preparada;
nenhuma fonte de plugin removida.
Escrito em 2026-07-25.
**Revisado e iniciado em 2026-07-31** (auditoria de 25 agentes + tranche de fundações):
todos os números medidos foram re-medidos. **Nenhuma linha da matriz de paridade e nenhuma
linha da matriz de testes sobreviveu intacta.** As correções estão marcadas com 🔄 e a data.

> **Estado honesto desta tranche (2026-07-31).** Foram adicionados o builder reproduzível de
> ZIP, o resolver de fonte `assets/` → `storages/`, o loader DB-free para módulos de teste, o
> provider sintético e a fixture de autenticação com teardown. Em 01/08, cinco artefatos
> reconciliados foram enviados ao `whatsbot-pro-plugins` no
> [PR #1](https://github.com/Techify-one/whatsbot-pro-plugins/pull/1): `protocolos` 1.24.0,
> `telegram` 1.3.1, `website` 1.1.1 e as primeiras publicações de `facebook_messenger`
> 1.5.0 e `instagram` 2.2.0. O PR permanece draft até confirmar a ordem de release com o
> core `developer` (`440536b`). **Nenhuma pasta de `assets/plugin_examples/` foi removida.**
> P4, a migração dos testes e a suíte completa continuam bloqueando F1–F7.

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

**A análise estática e o teste de boot indicam que é viável e que nenhum plugin tem
acoplamento de RUNTIME intransponível.** `plugins/bootstrap.py` só auto-instala `gowa`
(`BUNDLED_AUTO_INSTALL = ("gowa",)`, `bootstrap.py:37`). Nesta tranche,
`test_app_boots_with_no_plugin_source_folders` subiu o app com `assets/plugin_examples/` e
`storages/plugins/` vazios, confirmou registry de plugins vazio e bateu no endpoint real de
providers. O único provider retornado foi o GOWA de compatibilidade do core (plano 100 F2
ainda bloqueado). O gate empírico que faltava, portanto, foi fechado sem fingir que GOWA já
foi extraído.

O que quebra é (a) a suíte de testes e (b) a distribuição. Há **quatro pré-requisitos**
antes de apagar qualquer pasta. As fundações de P1–P3 começaram a existir nesta tranche,
mas nenhum dos gates de remoção/publicação foi fechado por completo.

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
| 🔄 `facebook_messenger` | **fora do plano original** | alto | artefato inédito no PR externo #1; 38 testes ainda no core |
| 🔄 `instagram` | **fora do plano original** | alto | artefato inédito no PR externo #1; 28 testes ainda no core |

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

🔄 **Estado após a tranche de 2026-07-31:** os ZIPs continuam gitignorados e fora do clone,
mas agora existe `scripts/build_plugin_zips.py`, com build determinístico, validação de
manifest, exclusão de caches/bancos locais e modos `--all`, `--list` e `--check`. O
`assets/channel_plugins/README.md` passou a apontar para ele. Isso resolve **reprodução do
artefato**, não sua disponibilidade: ainda não há CI, catálogo consumido pelo core nem
publicação automática, e um clone continua sem os ZIPs.

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

#### 🔄 Estado do repo de plugins (medido em 2026-07-31; publicação preparada em 2026-08-01)

O `Techify-one/whatsbot-pro-plugins` publica **16 plugins** (não 14), e os 3 rótulos de
versão (catalog.json, `<id>.json`, `plugin.yaml` dentro do zip) **batem nos 16** — a
inconsistência do commit `df71378` não se repetiu.

O [PR externo #1](https://github.com/Techify-one/whatsbot-pro-plugins/pull/1) adiciona
`instagram` e `facebook_messenger` e atualiza os três artefatos atrasados não-GOWA. Enquanto
o PR estiver draft/sem merge, `master` continua com 16 plugins; depois do merge serão 18.

#### 🔄 Matriz de paridade re-medida (2026-07-31)

| plugin | publicado em `master` → PR #1 | HEAD (git do core) | worktree (`assets/`) | instalado |
|---|---|---|---|---|
| `gowa` | **1.2.0** ⬅ atrás | 1.3.1 | 1.3.1 | 1.3.1 |
| `telegram` | 1.3.0 → **1.3.1** | 1.3.1 | 1.3.1 | 1.3.1 |
| `whatsapp_cloud` | 1.10.2 | 1.10.2 | 1.10.2 | 1.10.2 |
| `website` | 1.0.0 → **1.1.1** | 1.1.1 | 1.1.1 | 1.1.1 |
| `melhorias` | 1.7.0 | 1.7.0 | 1.7.0 | 1.7.0 |
| `protocolos` | 1.23.0 → **1.24.0** | 1.24.0 | 1.24.0 | 1.23.0 |
| `facebook_messenger` | ausente → **1.5.0** | 1.5.0 | 1.5.0 | — |
| `instagram` | ausente → **2.2.0** | 2.2.0 | 2.2.0 | 2.2.0 |

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

> ✅ **Atualização de 2026-08-01:** o GOWA publicado foi sincronizado com o bundled
> 1.3.1, inclusive permissão `channel.manage` e auditoria da configuração de alertas.
> O ZIP externo continua sendo um artefato de atualização; fresh install e upgrade
> automático ainda usam a cópia bundled do core.

**Ação / estado:**
1. ✅ Reconciliar plugin a plugin, comparando **conteúdo**, inclusive o GOWA 1.3.1.
2. ✅ Criar `scripts/build_plugins.py` no repositório externo. O builder usa
   `plugins/<id>/src/` como fonte, gera ZIP determinístico e rejeita testes, caches,
   bancos e segredos no artefato.
3. 🟡 🔄 **Resolver a disponibilidade:** os cinco ZIPs já chegaram a uma branch remota
   revisável; falta mergear o PR #1. O core ainda não consome `catalog.json`, então instalação
   pela UI continua exigindo baixar o ZIP e usar **Importar (.zip)**.
4. ⏳ Só então remover as pastas do core. **Nenhuma foi removida nesta tranche.**

### P2 — "O plugin traz os próprios testes" ✅ estrutura implantada

> ✅ **Atualização de 2026-08-01 (substitui o diagnóstico histórico abaixo):** os 18
> plugins do repositório Pro agora têm `src/` e `tests/`; os diretórios vazios são
> preservados no Git. Foram externalizados 28 arquivos Python, 5 suítes JavaScript e
> 2 ferramentas manuais. O core não descobre mais `storages/plugins/*/tests`: plugin
> instalado/atualizado em produção nunca executa nem recebe esses testes. O comando
> explícito é `python3 scripts/test_plugins.py <id>` no repositório externo; cada plugin
> roda em subprocesso separado contra um checkout compatível do core.
>
> O core foi reorganizado em `tests/core/`, `tests/contracts/` e `tests/integration/`.
> `WHATSBOT_PLUGIN_SOURCE_ROOT` permite que contratos transitórios usem o `src/` externo
> primeiro. As medições abaixo permanecem apenas como registro do estado encontrado antes
> dessa tranche; não descrevem mais a coleta atual.

A **descoberta** funciona: `tests/conftest.py::_discover_plugin_test_dirs` varre `storages/plugins/<id>/tests/test_*.py`
— mas só quando o pytest roda "pelado" (`args_source == TESTPATHS`).

**A falha da fixture foi corrigida nesta tranche.** `tests/support.py::_copy_plugin` agora
resolve primeiro `assets/plugin_examples/<id>` e depois `storages/plugins/<id>`, com erro que
cita os dois caminhos. `tests/plugin_test_utils.py` concentra a mesma resolução e carrega
pacotes/submódulos por path com `__path__`/`sys.modules` corretos para imports relativos. O
helper `loaded_plugin_module()` separa explicitamente esse carregamento unitário do módulo
real em `whatsbot_plugins.<id>`, evitando o falso-verde descrito abaixo.

🔄 **Correções de escala:**
- O `vendas_ia` tem **67 testes** embutidos (3 arquivos), não 2 — e continua sendo o **único
  plugin do sistema com testes de verdade dentro de si**. Nenhuma pasta de `assets/` tem
  diretório `tests/`; `storages/plugins/whatsapp_cloud/tests/` existe e está **vazio**.
  O objetivo declarado no cabeçalho ("o plugin traz tudo junto — inclusive os próprios
  testes") tem hoje **1 caso de sucesso em 18 plugins**. Criar o `tests/` dentro de cada
  plugin extraído é item explícito de cada fase, não consequência automática da F0.1.
- Os `monkeypatch` de `REAL_PLUGIN_EXAMPLES` são **10 arquivos**, não 6 (+67% em 6 dias):
  `test_avaliacao_protocolo`, `test_plano67_protocolos_toggle`,
  `test_plano68_reopen_assign_on_due`, `test_protocolos_popup`,
  `test_protocolos_relink_attr`, `test_utm_atendente`, `test_protocolos_ai_takeover`,
  `test_protocolos_atendente_provisorio`, `test_protocolos_mirror_sync` e
  `test_protocolos_skip_conditions`.
  **O número cresce a cada plano executado.**

#### 🚨 P2 tem um lado inverso que ninguém mediu

**112 testes do core dependem de conteúdo GITIGNORADO em `storages/plugins/`** —
`utm_atendente` 56, `retorno_automatico` 37, `vendas_ia` 10, `agendamento_retorno` 9. O bug
de coleta foi fechado nesta tranche: `test_utm_atendente.py` agora faz skip de módulo antes
de qualquer load quando a fonte instalada falta, e os módulos que intencionalmente exercitam
`protocolos`/`agendamento_retorno` instalados têm guard explícito. Isso torna o clone limpo
coletável; **não substitui mover esses testes para os plugins distribuídos** — ausente a
fonte, a cobertura é declaradamente skipada.

🔄 **Existir também não prova paridade.** Na validação desta tranche, a cópia instalada de
`agendamento_retorno` (1.4.0) existe, então o guard libera o módulo, mas seu
`build_message_text(description, contact_name)` já diverge do teste do core, que ainda chama
uma terceira posição `scheduler`. Esse vermelho é a prova dinâmica do split que o plano quer
eliminar: até o teste viajar junto do zip, o gate por existência evita erro de coleta, mas
não pode prometer compatibilidade de conteúdo.

O mesmo ocorreu com `protocolos`: o teste de atendente provisório exige 1.24/migration 019,
enquanto a cópia instalada local é 1.23 e termina na migration 018. O guard agora verifica a
capability concreta (arquivo 019), não só a existência da pasta, e declara skip em vez de
executar contra bytes incompatíveis/schema residual.

É **o mesmo bug de classe** do split `assets`↔`storages` que este plano usa como argumento
central da F1 — do outro lado. O `_copy_plugin` bidirecional não resolve isso sozinho.

**Ação / estado:**
1. ✅ `_copy_plugin` procura em `assets/plugin_examples/<id>` **e** `storages/plugins/<id>`,
   nessa ordem, com erro citando os dois caminhos.
2. ✅ 🔄 Um helper único de "carregar módulo de plugin nos testes", que também procura nos
   dois lugares e suporta imports relativos. Sem isso,
   extrair uma pasta quebra o arquivo de teste em **dois lugares independentes**: o
   `build_app` e o loader por caminho do próprio teste.
3. ✅ 🔄 Guard de skip obrigatório nos testes que **intencionalmente** apontam para
   `storages/plugins/`; consumidores que não precisam da cópia instalada passaram ao resolver
   dual e continuam rodando contra `assets/` no checkout atual.
4. ✅ 🔄 A fixture `authenticated_admin` autentica com admin isolado, restaura o header,
   apaga a sessão e remove o usuário que criou no teardown. Rotas de
   plugin gateadas por `core_permission` quebram **por ordem de execução**, porque o plano 48
   fecha a API assim que existe ≥1 usuário e o banco de teste é compartilhado pelo processo.
   A costura do fake provider, a prova da fixture `plugin_app` e a caracterização do sandbox
   já a adotaram; novos testes autenticados devem usar a mesma fixture.

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

**Ação / estado:** ✅ `tests/fake_provider.py` fornece `FakeChannel` configurável para o
contrato genérico e reexporta o `FakeGowaClient` sem fingir que o cliente HTTP é um
`Channel`. Há cobertura DB-free para descriptor, identidade, capabilities, limites/janelas,
lifecycle, outbound e `parse_inbound`. ⏳ Os ~90 testes do core ainda não foram migrados para
ele; portanto os fakes locais não podem ser apagados ainda.

> 🔄 **São 7 fakes de `Channel`, não 5 — e as linhas citadas envelheceram:**
> `_FakeChannel`, `_FakeWindowed`, `_FakeTplChannel` e `_P76GuardChannel` em
> `test_endpoints.py`; `_CloudStub` em `test_source_id_per_channel.py`; `_FakeChan` em
> `test_channel_identity_hooks.py`; e `_Meta` em `test_meta_graph_core.py`. Os símbolos,
> localizáveis por `rg`, são a referência estável.
>
> 🔄 **E há um 8º dublê que a varredura por subclasse não pega:**
> `tests.fakes.FakeGowaClient`. Não é um `Channel` — é o cliente GOWA falso, e é o que
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
> 🔄 **A lista de carregamentos em nível de módulo estava incompleta e desatualizada.** A
> busca pelos helpers de carregamento em `test_endpoints.py` inclui também o bloco inteiro de
> `protocolos` (que lê de `storages/`) e há ~18 call sites em 11 outros arquivos. Localize-os
> pelos símbolos do loader, não por números de linha. **O raio de explosão de remover uma
> pasta é MAIOR do que o plano descrevia.**

### 🔄 P4 (NOVO) — A superfície de import não é versionada

Os plugins de `assets/` fazem **100+ imports de módulos internos do core** (45 de
`db.repositories`, 30 de `plugins.context`, 14 de `channels.base`, mais `server.routes.*`,
`server.background`, `server.authz`, `ai_engine`, `app.services`, `gowa.manager`,
`runtime.supervisor`, `db.tables`, `agent`) — **nenhum é API declarada**.

E **os 22 manifests/cópias não ocultos medidos** (15 ids únicos; `assets` + `storages`
incluem duplicatas) declaram o mesmo `whatsbot_api_version: ">=1.0,<2.0"` contra
`WHATSBOT_API_VERSION = "1.0.0"` (`plugins/semver.py:26`), que **nunca foi bumpada** desde a
criação do sistema de plugins. **O guard nunca rejeitou nada e, como está, nunca vai
rejeitar.**

Hoje o **único** detector de "refactor do core quebrou o plugin" é a fonte do plugin estar
no repo e a suíte exercitá-la. Este plano **remove esse detector sem substituto**, num repo
**sem CI** (não existe `.github/`) e sem check de versão remoto.

O frontend avançou parcialmente nesta tranche: `plugin_services_version` agora negocia
surfaces 1.x/2.x de `api.services`; manifests legados caem no adapter 1.x e ranges inválidos
ou incompatíveis falham fechados. Isso **não fecha P4**: os 100+ imports Python e o
`WHATSBOT_API_VERSION` continuam no estado acima.

**Ação — uma das duas, obrigatória antes de qualquer remoção F1–F7:**
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

### F0 — Pré-requisitos (EM ANDAMENTO; bloqueia toda remoção)
1. ✅ `tests/support.py::_copy_plugin` procura em `assets/` **e** `storages/plugins/` (P2).
   Os 🔄 **10** `monkeypatch` que escolhem deliberadamente a cópia **instalada** foram
   preservados com guard; eles somem quando os respectivos testes forem para o plugin, não
   por uma troca silenciosa da fonte sob teste.
2. ✅ Fundação em `tests/fake_provider.py` para consolidar os 🔄 **7 fakes + o
   `FakeGowaClient`** num provider
   sintético completo: `provider_descriptor()`, `contact_type()`, `source_id_for()`, hooks de
   identidade, `media_limits`, `session_window_hours`, `capabilities` parametrizáveis, e
   `parse_inbound` capaz de emitir 🔄 `kind ∈ {message, system, receipt, reaction, edited}`.
   ⏳ A migração dos testes/fakes existentes continua pendente.
3. ✅ `scripts/build_plugin_zips.py` — build reproduzível dos zips (P1).
4. 🟡 🔄 **Resolver a disponibilidade do zip no servidor**: PR externo #1 aberto; falta merge.
5. 🟡 Cinco artefatos enviados ao `whatsbot-pro-plugins`; nenhuma fonte será removida antes
   do merge e dos gates P2–P4.
6. ✅ 🔄 Helper único de load, fixture de auth com teardown, guards de skip e primeiras
   adoções pelos testes existentes entregues (P2 · ações 2-4). A migração física dos testes
   para cada zip continua pertencendo às fases F1–F7.
7. ⏳ 🔄 Decidir a saída do P4 (contract test **ou** superfície versionada).

**Gate ainda NÃO atingido por completo:** os testes unitários das fundações e o 🔄 **boot
real do app com as duas pastas de fontes vazias** passaram. Falta fechar a suíte completa e,
principalmente, P1/P4. Até lá, nenhuma fase autoriza publicar/remover uma fonte de plugin.

**Validação desta tranche (31/07):** a costura/fundações passou em rodadas focadas, os 54
testes nativos de endpoint passaram e o legado `tests/test_endpoints.py` executou **1.641
checks, 0 falhas**. O `pytest tests/` agregado ainda não é um gate verde do baseline: além
da divergência instalada acima, `test_audit_matrix_is_complete` acusa corretamente 9 eventos
auditáveis adicionados em 27/07 sem suas células/goldens, e `test_alembic_hygiene` ainda
proíbe merge revisions embora `0058_merge_p50_p57` já exista. Nenhum desses vermelhos foi
silenciado para fabricar uma suíte verde.

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
em seis funções, localizáveis pelo símbolo), 1 sem mudança. 🔄 **Reconfirmado por função em
2026-07-31 — a única fase cujos números sobreviveram.** Pode ir para execução sem re-medição.

### F4 — `telegram`
Cuidado com os carregamentos em nível de módulo (lista corrigida no P3) — um erro aborta o
arquivo inteiro.

### F5 — `whatsapp_cloud` (o mais entrelaçado)
🔄 Ficou **maior e mais autônomo ao mesmo tempo**: o plano 84 acrescentou 4 arquivos + 1
migration (`alerts.py` 801 linhas, `filters.py` 162, `events.py` 22, `lifecycle.py` 51) e o
teste `test_plano84_account_alerts.py` (41 funções/1.000 linhas). O motor ficou no plugin;
o core recebeu somente o seam genérico de confiança (`provider`, `channel_id`,
`signature_authenticated`) descrito no plano 100.

As ferramentas manuais que antes estavam em `tests/manual_cloud_api_test.py` e
`tests/manual_inbound_message_inject.py` foram movidas para
`plugins/whatsapp_cloud/tests/manual/` no repositório externo e ficaram fora da coleta
automática.

Fica no core, e **está certo que fique** (valores duplicados, não import do plugin):
`LEGACY_CLOUD_VIDEO_LIMITS` em `channels/video_validate.py`, consumido por
`video_transcode.py` — fallback retrocompat para canal `windowed` cujo plugin não declara
`media_limits`.

### 🔄 F6 / F7 — `facebook_messenger` e `instagram` (NOVOS)
Fora do plano original. Os primeiros ZIPs estão no PR externo #1, portanto o bloqueio de
existência está encaminhado, mas só termina no merge. `facebook_messenger` ainda tem 38
testes em 2 arquivos no core, e `instagram` 28; P2–P4 continuam bloqueando a remoção.

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
> **Prova em produção:** uma instância real roda um core cujo `assets/` tem telegram
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
> `facebook_messenger` nem `instagram`**. O PR externo #1 corrige esses cinco casos, mas não
> o `gowa`. Um check ingênuo ainda diria *"gowa desatualizado"* para
> uma instância que está na versão MAIS NOVA, e o "update" seria um **downgrade que o próximo
> boot desfaz sozinho**. **O catálogo só vira fonte de verdade DEPOIS que o canal sair do
> core** — enquanto os dois coexistirem, o check tem de ignorar plugin bundled.

> 🔄 **Contrapartida positiva, com o plano 84 já corrigido:** quanto menor o seam no core,
> mais autônomo o zip. Isso não elimina automaticamente a ordem de deploy: o alerta de webhook
> da Meta exige a procedência/autenticação do core novo e degrada fechado no anterior. **O
> critério por fase é: o plugin carrega no core anterior; dependências de seam novo degradam
> explicitamente e têm ordem de deploy documentada.**

---

## 6. Resumo da recomendação

| Fase | Recomendação |
|---|---|
| 🔄 **Plano 100 antes de tudo** | F0 segura concluída e F1 confirmado; F2 GOWA ainda bloqueado pelos contratos/gates |
| F0 | **Estrutura implantada:** 18 plugins com `src/tests/json/zip`, builder e runner externos; ZIPs não contêm testes. Remoção dos espelhos do core ainda depende do legado. |
| F1 `protocolos` | **Testes e fonte publicados externamente**; espelho no core mantido temporariamente porque `legacy_endpoints` ainda o carrega. |
| F2 `melhorias` | **Testes e fonte publicados externamente**; os testes claros saíram da suíte do core. |
| F3 `website` | **Testes e fonte publicados externamente**; os testes claros saíram da suíte do core. |
| F4/F5 `telegram`/`whatsapp_cloud` | **Parcial:** fonte externa e testes Cloud migrados; contratos mistos e blocos legados ainda precisam ser fatiados antes de apagar os espelhos. |
| 🔄 F6/F7 `facebook_messenger`/`instagram` | **Fonte e testes externos**; o contrato Meta Graph também saiu do core. Remoção dos espelhos segue o gate do legado. |

---

## 7. 🔄 Áreas correlatas que a auditoria encontrou (não bloqueiam, mas contaminam)

- **`custom_sounds` foi absorvido na direção CONTRÁRIA** (plugin → core): `server/sound_catalog.py`
  (180 linhas), `server/routes/sound_prefs.py` (212), `db/repositories/custom_sound_repo.py`,
  a tabela `custom_sounds` e permissões na migration `0062`. O `CLAUDE.md` já foi alinhado
  para registrar essa absorção; ela continua sendo precedente importante da direção oposta.
- **Migrations com provider hardcoded:** `0047_source_id_native.py` tem 3 cláusulas
  `c.provider <> 'gowa'` e `0050_contact_type.py` escreve `contact_type='telegram'` filtrando
  por `ch.provider='telegram'`. São **fósseis legítimos** (migration é histórico imutável) —
  mas precisam estar declarados como exceção, senão a próxima auditoria de `if provider ==`
  os reporta como violação.
- **Não existe `package.json`**: os testes JS (`node --test`) não têm runner nem manifesto e
  são invocados à mão. Os 5 `.test.js` que já moram dentro de plugins **não rodam em lugar
  nenhum automaticamente**. O P2 trata só do lado Python; o lado JS de "o plugin traz seus
  testes" já existe e já é inexecutável.
