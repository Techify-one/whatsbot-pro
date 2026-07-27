# Plano 83 — Extrair os plugins do repositório do core

**Status:** PLANEJADO — nada executado. Escrito em 2026-07-25.

**Objetivo do usuário:** o repositório `whatsbot-pro` (core) não deve mais carregar o
código-fonte dos plugins em `assets/plugin_examples/`. Cada plugin vira um `.zip`
distribuído pelo repo `whatsbot-pro-plugins`, o usuário instala por **Importar (.zip)**
e o plugin traz **tudo** junto — inclusive os próprios testes. No core fica só o `gowa`.

---

## 1. Veredito

**É viável, e nenhum plugin tem acoplamento de RUNTIME que impeça.** O app sobe sem
qualquer uma das 5 pastas: `plugins/bootstrap.py` só auto-instala `gowa`
(`BUNDLED_AUTO_INSTALL = ("gowa",)`), e as menções aos outros ids em `bootstrap.py`,
`server/app.py:176` e `plugins/context.py:253` são **comentários**.

O que quebra é (a) a suíte de testes e (b) a distribuição. E há **três pré-requisitos**
que precisam existir ANTES de apagar qualquer pasta — nenhum deles existe hoje.

| Plugin | Veredito | Risco | Acoplamento que quebra |
|---|---|---|---|
| `protocolos` | removível **hoje** | baixo | **nenhum** |
| `melhorias` | removível com trabalho | médio | 1 (só o `_copy_plugin`) |
| `website` | removível com trabalho | médio | 3 |
| `telegram` | removível com trabalho | alto | 5 |
| `whatsapp_cloud` | removível com trabalho | alto | 6 |

---

## 2. Os três pré-requisitos (bloqueadores)

### P1 — Os `.zip` não estão no git **deste** repo (mas os plugins já estão publicados)

```
$ git check-ignore -v assets/channel_plugins/telegram-plugin.zip
.gitignore:27:*-plugin.zip
$ git ls-files assets/channel_plugins/
assets/channel_plugins/README.md      ← só o README
```

Os 3 zips em `assets/channel_plugins/` são **artefato local de disco**, invisíveis pro
git. O próprio README declara: *"The source of truth lives in `assets/plugin_examples/<id>/`"*.
Não há script nem CI para gerá-los — o `README.md:32` manda rodar um snippet Python à mão.

**Boa notícia (verificado em 2026-07-25):** o repo `Techify-one/whatsbot-pro-plugins`
(privado, branch `master`) **já publica os 14 plugins**, incluindo os 5 deste plano:

| plugin | versão publicada |
|---|---|
| `protocolos` | 1.21.0 |
| `melhorias` | 1.4.0 |
| `telegram` | 1.3.0 |
| `whatsapp_cloud` | 1.4.0 |
| `website` | 1.0.0 |
| `gowa` | 1.2.0 |

Então P1 **não é mais um bloqueador de existência** — é um bloqueador de **paridade**.

> ⚠️ **Higiene de versionamento no repo de plugins.** Duas inconsistências achadas:
> 1. O commit `df71378` se chama *"protocolos 1.21.1"* mas o `plugin.yaml` dentro do zip
>    diz `version: 1.21.0`, e o `catalog.json` também diz 1.21.0. O rótulo do commit e o
>    conteúdo não batem.
> 2. O `protocolos.zip` publicado (145.910 bytes, 24/07 11:21) é **conteúdo mais ANTIGO**
>    que o zip de produção em circulação (147.094 bytes, 24/07 19:38) — ambos rotulados
>    1.21.0. O publicado não tem a toolbar fundida no topo nem o popover ⚙ de origem/config
>    de filtros (260 linhas de diferença em `static/protocolos_tab.js`).
>
> É o **mesmo padrão** já registrado no projeto: *número de versão maior (ou igual) não
> garante conteúdo maior*. Ao comparar plugins, compare SEMPRE o conteúdo.

#### Matriz de paridade medida em 2026-07-25

Os 4 lugares do CLAUDE.md, com versões reais lidas de cada `plugin.yaml`:

| plugin | publicado (repo de plugins) | HEAD (git do core) | worktree (WIP p81) | instalado |
|---|---|---|---|---|
| `gowa` | 1.2.0 | 1.2.0 | 1.2.1 | 1.2.1 |
| **`telegram`** | **1.3.0** ⬅ maior | 1.2.0 | 1.2.1 | 1.2.1 |
| **`whatsapp_cloud`** | 1.4.0 | **1.5.0** ⬅ maior | 1.6.0 | 1.6.0 |
| `website` | 1.0.0 | 1.0.0 | 1.0.1 | 1.0.1 |
| `melhorias` | 1.4.0 | 1.4.0 | 1.4.0 | 1.4.0 |
| **`protocolos`** | 1.21.0 *(conteúdo velho)* | 1.20.0 | 1.20.0 | **1.21.0** *(conteúdo novo)* |

> 🚨 **As linhagens divergem nos DOIS sentidos.** Não existe um lado que seja
> "a verdade":
> - `telegram`: o **publicado** tem 42 linhas em `channels.py` que o core não tem
>   (helpers `_to_int`, `_to_float`, `_send_result`). Publicar a partir do core
>   **regrediria** o plugin.
> - `whatsapp_cloud`: o **core** está à frente do publicado.
> - `protocolos`: publicado e instalado têm o **mesmo número** (1.21.0) e conteúdos
>   diferentes — o instalado é 8 h mais novo.
>
> Uma reconciliação por-plugin, comparando CONTEÚDO, é pré-requisito de qualquer
> remoção. Fazer o build a partir de `assets/` e publicar em lote **destrói trabalho**.

**Ação:**
1. Reconciliar plugin a plugin o `whatsbot-pro-plugins` com o conteúdo realmente mais
   novo, com bump de versão de verdade quando o conteúdo mudar (o rótulo do commit não
   basta — ver `df71378`). **Decisão do dono do repo**, não automatizável.
2. Criar um script de build reproduzível (`scripts/build_plugin_zips.py`) no lugar do
   snippet manual do README, para o zip publicado nunca mais divergir da fonte.
3. Só então remover as pastas do core.

### P2 — "O plugin traz os próprios testes" só funciona pela metade

A **descoberta** funciona: `tests/conftest.py:162` (`pytest_configure` +
`_discover_plugin_test_dirs`) varre `storages/plugins/<id>/tests/test_*.py` e anexa aos
roots de coleta — mas só quando o pytest roda "pelado" (`args_source == TESTPATHS`).

A **fixture que sobe o app não funciona**. `tests/support.py:74-78`:

```python
src = REAL_PLUGIN_EXAMPLES / plugin_id          # = assets/plugin_examples/<id>
if not src.is_dir():
    raise ValueError(f"build_test_app: unknown bundled plugin {plugin_id!r} ...")
```

Prova empírica (o `vendas_ia` é justamente o plugin que já vive só em `storages/plugins/`
e já tem `tests/` embutido):

```
melhorias:  COPIADO OK
vendas_ia:  FALHA -> build_test_app: unknown bundled plugin 'vendas_ia'
```

Ou seja: **um teste que mora no plugin não consegue subir o app com o próprio plugin.**
O `vendas_ia` só se safa porque seus 2 testes não bootam app.

**Ação (mudança no core, pequena):** `_copy_plugin` passa a procurar em
`assets/plugin_examples/<id>` **e** em `storages/plugins/<id>`, nessa ordem, com
mensagem de erro citando os dois caminhos. Isso também elimina o `monkeypatch` de
`REAL_PLUGIN_EXAMPLES` que 6 arquivos de teste hoje fazem à mão
(`test_avaliacao_protocolo.py:32` e irmãos) — vira comportamento padrão.

### P3 — 107 testes do core usam plugins como veículo

Não são testes do plugin: testam **comportamento do core** e só precisam de *um*
provider qualquer que responda. Distribuição:

| Plugin | testes que **vão pro plugin** | testes do **core** que precisam de substituto |
|---|---|---|
| `whatsapp_cloud` | 78 | 61 |
| `telegram` | 8 | 46 |
| `website` | 16 | 6 |
| `melhorias` | 40 | 0 (os 14 restantes já são core puro) |
| `protocolos` | 255 | 0 |

Exemplos do que é core: `test_plano82_system_inbound.py` (dispatch de `kind='system'` em
`server/routes/channel_webhook.py`), `test_plano75_error_card.py` (card de erro a partir
de `statuses[].status == "failed"`), `test_plano75_failed_race.py` (corrida de status),
`test_audit_characterization.py`. O plugin só fornece um `parse_inbound` que devolve o
`kind` certo.

**Ação:** um provider sintético residente em `tests/` (não em `assets/plugin_examples/`).

> **Não precisa inventar.** O core já tem **5 fakes de `Channel`** espalhados, o mais
> completo em `tests/test_endpoints.py:4869` (`_FakeChannel`, `provider="test"`, já
> registrado no registry vivo, já exercitando `GET /api/channels/providers`,
> `OutboundRouter` e um ingest e2e). Há também `_FakeWindowed` (:4944), `_FakeTplChannel`
> (:6134), `_CloudStub` (`test_source_id_per_channel.py:26`) e `_FakeChan`
> (`test_channel_identity_hooks.py:103`). E `ALLOWED_PROVIDERS` em
> `app/services/channel_service.py:105` **já reserva** o nome `"test"`.
>
> O antigo plugin `channel_test` (removido em `4eafca1`, 27/06/2026) é recuperável com
> `git show 4eafca1^:assets/plugin_examples/channel_test/channels.py`, mas está obsoleto
> (era da era do plano 02 — sem `provider_descriptor()`, `contact_type()`,
> `source_id_for()`, hooks de identidade, `media_limits`, `session_window_hours`, e só
> sabe emitir `kind="message"`). Serve de referência de estilo, não de base.
>
> O trabalho é de **consolidação dos 5 fakes existentes** num `tests/fake_provider.py`,
> não de invenção.

---

## 3. O que a branch `plano-76` já resolve

`origin/plano-76-desacoplar-providers` (4 commits, 52 arquivos, +3767/−248) já fez a
parte mais chata do desacoplamento de **frontend**:

- cria `web/static/js/services/providerCatalog.js` + `hooks/useProviderCatalog.js`;
- tira nomes de provider hardcoded de `contactTypes.js`, `ChannelChip.js`,
  `ChannelPickerModal.js`, `ConversationFilterDialog.js`, `NewConversationModal.js`,
  `ConversationInfoPanel.js`, `services/api.js`;
- **move `WebhookHealthRow.js` para fora do core** (`R070` →
  `assets/plugin_examples/whatsapp_cloud/static/`), resolvendo a exceção que o CLAUDE.md
  admitia desde o plano 33 P2, via slot genérico `channel.card.rows`.

**Este plano pressupõe a `plano-76` já mergeada.** Sem ela, some um item de trabalho
por plugin (des-hardcodar o provider no frontend).

---

## 4. Fases

### F0 — Pré-requisitos (bloqueia tudo)
1. `tests/support.py::_copy_plugin` procura em `assets/` **e** `storages/plugins/` (P2).
   Remover os 6 `monkeypatch` de `REAL_PLUGIN_EXAMPLES` que viram redundantes.
2. `tests/fake_provider.py` — consolida os 5 fakes num provider sintético completo:
   `provider_descriptor()`, `contact_type()`, `source_id_for()`, hooks de identidade,
   `media_limits`, `session_window_hours`, `capabilities` parametrizáveis, e
   `parse_inbound` capaz de emitir `kind ∈ {message, system, status, receipt}` (P3).
3. `scripts/build_plugin_zips.py` — build reproduzível dos zips (P1), substituindo o
   snippet manual do `assets/channel_plugins/README.md`.
4. Clonar `whatsbot-pro-plugins` e publicar os 5 plugins lá **antes** de remover (P1).

**Gate:** suíte verde com o `_copy_plugin` novo, sem nenhuma pasta removida ainda.

### F1 — `protocolos` (o mais fácil, zero acoplamento)
Já hoje **nenhum** teste depende da cópia em `assets/` — os 6 arquivos fazem
`monkeypatch` para `storages/plugins/`. Após F0.1 nem o monkeypatch é preciso.
1. Publicar `protocolos` 1.21.0 no `whatsbot-pro-plugins`.
2. `git rm -r assets/plugin_examples/protocolos`.
3. Rodar `tests/test_endpoints.py` + os 4 arquivos de protocolos.

> ⚠️ **Atenção de higiene:** o split assets↔storages do `protocolos` já causou um bug
> real e silencioso — `test_endpoints.py` abortava na linha 3328 com
> `AttributeError: ... has no attribute '_resolve_opener'` porque carregava de
> `storages/` (1.19.0) enquanto a função só existia em `assets/` (1.20.0). **746 de 1570
> checks (48%) e 31 seções nunca rodavam, com 0 FAIL aparente.** Corrigido em 2026-07-25
> instalando a 1.21.0. Ter UMA fonte por plugin elimina essa classe de bug — é o
> principal argumento técnico a favor deste plano.

### F2 — `melhorias` (risco baixo, 0 testes de core)
Os 18 testes (`test_melhorias_plugin.py` 9 + `test_p51_melhorias_gateway.py` 9) usam
`plugin_app("melhorias")` — todos testam o **plugin**. Mais 22 testes JS que já moram no
próprio plugin (`static/chat_core.test.js` 7, `static/markdown.test.js` 15).
Os 14 restantes (`test_sandbox_improve_characterization.py`, `test_p51_execution_trace.py`,
`systemCta.test.js`) são core puro e não mudam.
1. Mover os 18 para `<plugin>/tests/`. 2. Publicar. 3. Remover a pasta.

### F3 — `website` (6 testes de core)
`test_website_widget.py` tem 23 testes: 16 do plugin, 6 do core (`build_app(["gowa","website"])`
nas linhas 257/269/277/290/298/311), 1 sem mudança.
Trocar os 6 pelo fake de F0.2, mover os 16, publicar, remover.

### F4 — `telegram` (46 testes de core)
Cuidado: `tests/test_endpoints.py:690` faz `_load_provider("tg_test", "assets/.../telegram/channels.py")`
em **nível de módulo** (idem 5052, 5263, 6681/6690) — um erro aborta o script inteiro.
Idem `test_plano75_parse_inbound.py:61`. Trocar por fake ou por carregamento de
`storages/`. `test_audit_characterization.py:121` e `test_plugin_test_discovery.py:148`
também.

### F5 — `whatsapp_cloud` (61 testes de core, o mais entrelaçado)
Além dos call sites de nível de módulo (`test_endpoints.py:683/6439/6689`,
`test_plano75_cloud_inbound_text.py:22`, `test_plano75_parse_inbound.py:29`), há
`tests/manual_cloud_api_test.py:25` que faz `from assets.plugin_examples.whatsapp_cloud.channels import ...`
(import de pacote real via namespace package).

Fica no core, e **está certo que fique** (são valores duplicados, não import do plugin):
`channels/video_validate.py:36` `LEGACY_CLOUD_VIDEO_LIMITS` + `video_transcode.py:30`
— fallback retrocompat para canal `windowed` cujo plugin não declara `media_limits`.

---

## 5. O custo que este plano NÃO elimina: atualização manual

Existe `POST /api/plugins/{id}/update` (`server/routes/plugins.py:425-513`) + botão
**"Atualizar"** (`PluginsManager.js:353`), que preserva tabelas `plugin_<id>_*`, settings
`plugin.<id>.*`, `plugin_migrations` e o flag `enabled`, roda migrations pendentes e
avisa em downgrade. `POST /api/plugins/import` recusa plugin já instalado — update é o
único caminho in-place.

**Mas não há catálogo remoto nem check de versão.** Grep por `catalog.json` /
`whatsbot-pro-plugins` / `marketplace` nos `.py`/`.js` do core: **zero hits**. O único
link é estático para a Loja *community* (`PluginsManager.js:270-274`), que é outro repo.

Contraste com o `gowa`: `plugins/bootstrap.py:127-173` (`_upgrade_bundled_gowa_in_place`)
compara semver e substitui `storages/plugins/gowa` no boot — **automático a cada
`git pull`/redeploy**.

> **Trade-off explícito:** tirar os providers do core troca *"atualiza sozinho no deploy"*
> por *"o operador baixa o zip e clica Atualizar, instância por instância"*, sem
> notificação de versão nova. Para `melhorias`/`protocolos` isso já é a realidade hoje.
> Para os **canais** é uma regressão operacional — e pior quando a mudança é entrelaçada
> com o core (obriga ordem: core primeiro, zip depois).
>
> **Mitigação sugerida (fora do escopo deste plano):** um check de versão contra o
> `catalog.json` do `whatsbot-pro-plugins`, com aviso no card do plugin. Sem isso, F4/F5
> merecem ser adiadas mesmo com F0 pronto.

---

## 6. Resumo da recomendação

| Fase | Recomendação |
|---|---|
| F0 | Fazer. Vale por si só (F0.1 elimina a classe de bug do split assets↔storages). |
| F1 `protocolos` | Fazer junto com F0. Custo ~zero, ganho real. |
| F2 `melhorias` | Fazer. Risco baixo, 0 testes de core afetados. |
| F3 `website` | Fazer depois de F0.2 (só 6 testes a portar). |
| F4/F5 `telegram`/`whatsapp_cloud` | **Adiar** até existir check de versão remoto. São os 2 com testes de core em nível de módulo e são **canais** — o custo operacional da atualização manual é maior. |
