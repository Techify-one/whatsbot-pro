# Plano 108 — Instalação limpa para cliente: podar o espelho de plugins, tirar dados de produção da árvore e publicar a escada de 6 releases

> **Status:** ✅ **EXECUTADO** (2026-08-07) — `origin/main` em `76a732b`, 6 releases no ar (`v0.2.0`…`v0.7.0`), só a `v0.7.0` como *Latest*. Pendências abertas: **P2 (licença)** e o commit do repositório de plugins. · **Data:** 2026-08-07 · **Escopo:** grande (zero linha de core alterada; 178 arquivos removidos, 5 call sites de teste migrados, 6 releases publicadas)
> **Origem:** pedido do usuário — levar `developer` (estável) para `main` e publicar release nova num repositório **público**, em degraus, "para não pegar muita gente de surpresa". Durante a investigação o escopo cresceu: o `developer` carrega **PII de clientes reais** e o espelho `assets/plugin_examples/` publica a fonte de 9 plugins cuja casa declarada é o repositório privado. **Método:** 3 workflows de investigação (32 sub-agentes) com verificação adversarial, mais leitura em 1ª mão e medição direta de cada afirmação (`git merge-base`, `diff -rq`, `grep -c`, `wc -l`).
> O objetivo final é uma **instalação limpa para o cliente**: core + canal GOWA, e o resto puxado sob demanda de uma loja de plugins. A poda **não é amputação de funcionalidade** — é o modelo de distribuição pretendido.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | ✅ (2026-08-07) **Só o `gowa` fica em `assets/plugin_examples/`.** Os outros 9 saem | 178 arquivos removidos. É coerente com [plugins/bootstrap.py:37](../plugins/bootstrap.py#L37) (`BUNDLED_AUTO_INSTALL = ("gowa",)`) e com o CLAUDE.md, que já chama a pasta de "espelho transitório" |
| D2 | ✅ (2026-08-07) **`protocolos` e `melhorias` saem junto**, mesmo já estando públicos na `main` desde julho | Exige migrar o call site de módulo [legacy_endpoints.py:2558](../tests/core/legacy/legacy_endpoints.py#L2558) **antes** da poda, senão a suíte legada inteira aborta no import |
| D3 | ✅ (2026-08-07) **Os 5 providers genéricos saem sem publicar `.zip` nas releases.** Distribuição é pela loja | As notas de release dos degraus que anunciam Telegram / Cloud / Messenger / Instagram / widget **precisam** dizer que o canal vem da loja. Ver F9·2 |
| D4 | ✅ (2026-08-07) **PII sai da árvore AGORA; reescrita de histórico é tarefa separada** | A Fase 0 desbloqueia o merge. A reescrita (`git-filter-repo` + force-push, precedente do plano 78) vira P1, fora deste plano |
| D5 | ✅ (2026-08-07) **Escada de 6 degraus** (v0.2.0 → v0.7.0) | É o menor número que isola as **duas quebras** (Postgres-only e fim da Senha do Painel) em notas próprias |
| D6 | ⏸️ (2026-08-07) **Licença adiada.** "Por enquanto é tudo liberado" | ⚠️ Sem arquivo `LICENSE`, o efeito jurídico é o **inverso** da intenção: ausência de licença = *todos os direitos reservados*. Fica como P2, marcado no plano, sem travar a execução |
| D7 | ✅ **Nenhuma costura de core sai.** Só código de plugin | As 20 costuras genéricas mapeadas em §3.2 entram na `main` normalmente |
| D8 | ✅ **Limpar o `developer` PRIMEIRO, depois merge trivial** | Estratégias alternativas (merge + `git rm`, merge seletivo, cherry-pick) filtram o sintoma a cada release e reintroduzem os arquivos no merge seguinte. Ver §6·R7 |

---

## 1. Resumo executivo

O `developer` está estável e pronto de conteúdo, mas **não está pronto para virar `main` de um repositório público**. Três coisas precisam acontecer antes, e a ordem importa:

1. **Sai da árvore o diretório `plano-merge-contatos-duplicados/`** — 9 arquivos com nome civil completo + telefone de 9 clientes reais, host do banco de produção e comandos de dump. Entrou de carona num commit sobre outra coisa.
2. **O espelho `assets/plugin_examples/` é podado até sobrar só o `gowa`** — 178 arquivos, 9 plugins. Verificado: o repositório privado é **superset estrito** de todos os nove; nada se perde.
3. **`README` e `CLAUDE.md` param de mentir** — o README manda executar um arquivo que não existe e não menciona o PostgreSQL obrigatório.

Só então o merge acontece — trivial, sem lista de exclusão — e a escada de 6 releases é publicada como rascunho, revisada e promovida em ordem crescente.

**Nenhuma linha de core é alterada por este plano.** A poda é possível justamente porque o core nunca conheceu esses plugins por nome.

---

## 2. Como funciona hoje (mapa)

### 2.1 Estado dos branches (medido)

| Fato | Valor | Como foi medido |
|---|---|---|
| `main` HEAD | `2bb942e` (2026-07-28) | `git log -1 main` |
| `developer` HEAD | `1f11809` (2026-08-06) | `git log -1 developer` |
| Commits `main..developer` | **70** | `git rev-list --count` |
| Diff | 527 arquivos, +42.609 / −11.859 | `git diff --shortstat main...developer` |
| Local vs `origin` | **idênticos nos dois branches** | `git rev-parse` |
| Releases no GitHub | **nenhuma** | `gh release list` (vazio) |
| Tags no GitHub | **nenhuma** | `git ls-remote --tags origin` (vazio) |

⚠️ **As 9 tags locais apontam para commits ÓRFÃOS.** `git merge-base v0.1.1 main` devolve vazio — a história de `v0.1.1` (`b34b577`) e a de `main` são **disjuntas**, resultado do `git-filter-repo` + force-push do plano 78 (2026-07-23).

Consequência prática: `git rev-list --count v0.1.1..main` = **782** apenas porque `main` tem 782 commits no total, sem nada em comum com a tag. O intervalo **real** parte do gêmeo reescrito da `v0.1.1` — `755cb1a` (2026-05-14, mesmo assunto e timestamp, hash diferente), que **é** ancestral de `main` — e mede **670 commits** até `developer`.

✅ **Isso é uma boa notícia:** sem nada publicado para contradizer, a numeração das releases é livre.

### 2.2 O que o core sabe sobre os plugins que vão sair

| Verificação | Resultado |
|---|---|
| `git grep -l -iE 'trackify\|retornos'` fora de `assets/`, `docs*`, `tests/`, `CLAUDE.md` | **2 arquivos, ambos só em comentário** — [messageView.js:117](../web/static/js/services/messageView.js#L117), [ContactDetail.js:851](../web/static/js/components/contacts/ContactDetail.js#L851) |
| Boot depende das pastas? | ❌ Não — [bootstrap.py:37](../plugins/bootstrap.py#L37) só copia `gowa`, e os dois caminhos têm guarda de existência (`if not child.is_dir(): continue`, [:58-59](../plugins/bootstrap.py#L58)) |
| Core importa de `assets/plugin_examples/`? | ❌ Nenhum módulo fora de `tests/` |
| Único ponto NOVO em que o core cita plugin por nome | [serviceSurface.js:28](../web/static/js/plugins/serviceSurface.js#L28) — path literal `/api/plugins/gowa/alert-settings`. É `gowa`, que **fica**. Não bloqueia |

### 2.3 O acoplamento REAL: cinco call sites de módulo na suíte legada

⚠️ **Este é o gotcha que torna a ordem das fases obrigatória.** [tests/core/legacy/legacy_endpoints.py](../tests/core/legacy/legacy_endpoints.py) tem **7.174 linhas** e é importado como módulo por [tests/core/test_legacy_scripts.py:25](../tests/core/test_legacy_scripts.py#L25). Cinco referências a plugin estão em **nível de módulo, sem guard** — não falham uma asserção, **abortam o import e derrubam a suíte inteira**:

| Linha | Plugin | Forma |
|---|---|---|
| [:683](../tests/core/legacy/legacy_endpoints.py#L683) | `whatsapp_cloud` | `_load_provider("wac_test", "assets/plugin_examples/whatsapp_cloud/channels.py")` |
| [:690](../tests/core/legacy/legacy_endpoints.py#L690) | `telegram` | `_load_provider("tg_test", …/telegram/channels.py)` |
| [:2558](../tests/core/legacy/legacy_endpoints.py#L2558) | `protocolos` | `_atd_dir = _resolve_plugin_source("protocolos")` |
| [:5123](../tests/core/legacy/legacy_endpoints.py#L5123) | `telegram` | `_tg_path = PROJECT_ROOT / "assets" / "plugin_examples" / "telegram" / "channels.py"` |
| [:5334](../tests/core/legacy/legacy_endpoints.py#L5334) | `telegram` | `_tg_mod = _load_provider("tg_edit_test", …)` |

Mais dois **dentro de função** (falham só o próprio teste, risco menor): [:6591](../tests/core/legacy/legacy_endpoints.py#L6591) (`whatsapp_cloud`) e [:6839](../tests/core/legacy/legacy_endpoints.py#L6839) (`plugin_dir = … / prov`, dinâmico).

✅ **A peça de substituição já existe**: [tests/fake_provider.py](../tests/fake_provider.py) e a fixture sintética coberta por [tests/contracts/test_fake_provider_integration.py](../tests/contracts/test_fake_provider_integration.py). Não é gambiarra — é o caminho que o plano 100 já preparou.

### 2.4 O resolvedor de fonte de plugin já prefere o repositório externo

[tests/plugin_test_utils.py:42](../tests/plugin_test_utils.py#L42) lê `WHATSBOT_PLUGIN_SOURCE_ROOT` e a precedência é `WHATSBOT_PLUGIN_SOURCE_ROOT/<id>/src` → `assets/plugin_examples/<id>` → `storages/plugins/<id>`. É literalmente a peça que torna esta poda possível: **não precisa de conserto, só de uso**.

---

## 3. Inventário

### 3.1 O que sai de `assets/plugin_examples/` (D1 · D2)

Verificado com `diff -rq --exclude=__pycache__` contra `../whatsbot-pro-plugins/plugins/<id>/src`:

| Plugin | Arq. | Privado tem? | Paridade | Custo de remover | Risco | Esforço |
|---|---:|---|---|---|---|---|
| `retornos` | 28 | ✅ src + zip | **idêntico** | zero — nenhum teste (`git ls-files \| grep test_retornos` = vazio) | baixo | S |
| `trackify` | 29 | ✅ src + zip | **idêntico** | mover [test_trackify_plugin.py](../tests/integration/test_trackify_plugin.py) (2.657 linhas, path fixo em [:27](../tests/integration/test_trackify_plugin.py#L27)) | baixo | M |
| `telegram` | 8 | ✅ src + zip | **idêntico** | ⛔ 3 call sites de módulo (`:690`, `:5123`, `:5334`) | **alto** | M |
| `facebook_messenger` | 9 | ✅ src + zip | **idêntico** | 1 arquivo de teste | baixo | S |
| `instagram` | 9 | ✅ src + zip | **idêntico** | zero | baixo | S |
| `website` | 13 | ✅ src + zip | **idêntico** | 1 arquivo de teste | baixo | S |
| `whatsapp_cloud` | 21 | ✅ src + zip | privado **1.10.3** > espelho 1.10.2 | ⛔ 1 call site de módulo (`:683`) + 1 em função (`:6591`) | **alto** | M |
| `melhorias` | 23 | ✅ src + zip | privado **1.7.1** > espelho 1.7.0 | zero (só citado em docstring de caracterização) | baixo | S |
| `protocolos` | 38 | ✅ src + zip | privado **1.28.0** > espelho 1.25.0 | ⛔ 1 call site de módulo (`:2558`) | **alto** | M |
| **Total** | **178** | | | | | |
| `gowa` | 11 | — | — | **PERMANECE** (auto-instalado) | — | — |

✅ **Verificação de perda de trabalho (obrigatória antes do `git rm`):** nenhum dos nove tem arquivo exclusivo do espelho. O único candidato aparente — `whatsapp_cloud/static/templateFilter.test.js` — existe no privado **renomeado** para `tests/js/template_filter.test.js`: 138 linhas, 19 testes, `diff` vazio ignorando linhas de import. Os outros três `.test.js` diferem **só** no caminho de import/comentário.

### 3.2 Costuras de core que FICAM (D7)

Nenhuma sai. As que este diff acrescentou, e que continuam valendo com o espelho podado:

| Costura | Local | Vale sem o plugin? |
|---|---|---|
| Procedência no `filter.webhook.payload` + `verify_inbound_signature_result` | [channels/base.py:581-598](../channels/base.py#L581), [channel_webhook.py:668-745](../server/routes/channel_webhook.py#L668) | ✅ é a fronteira de confiança do core |
| `is_redelivery` no `message.failed` | [channel_webhook.py:320-430](../server/routes/channel_webhook.py#L320), [plugins/events.py:48-56](../plugins/events.py#L48) | ✅ corrige ambiguidade do próprio contrato |
| `overrideComponent` (3ª semântica do registry) | [registry.js:70-79,171-202](../web/static/js/plugins/registry.js#L70) | ✅ cai no fallback do core |
| `TemplateSpec` declarado pelo provider | [channels/base.py:33-60](../channels/base.py#L33), [template_service.py:19-205](../app/services/template_service.py#L19) | ✅ `spec=None` = sem restrição |
| `plugin_services_version` (negociação fail-closed) | [App.js:64-76](../web/static/js/components/shell/App.js#L64), [versionCompat.js](../web/static/js/plugins/versionCompat.js) | ✅ infraestrutura pura |
| `api.services.subscribe` (wsBus autenticado, 2.1) | commit `7443fb2` | ✅ **já tem consumidor no core** — [ToolsUnified.js](../web/static/js/components/ai/ToolsUnified.js) |
| `pattern`/`pattern_error` de credencial | [channel_service.py:224-274](../app/services/channel_service.py#L224) | ✅ motivado pelo `gowa`, que fica |
| `secretInputProps` (bloqueia autofill) | [constants.js:129-204](../web/static/js/components/channels/constants.js#L129) | ✅ transversal a todos os providers |
| `conversation.created` no nascimento INBOUND | [message_listeners.py:149-196](../agent/message_listeners.py#L149) | ✅ ⚠️ **veio grudado no commit do trackify (`5c1681e`)** — a poda por commit o perderia |
| `isCollapsibleRole` → `isCollapsibleCard` | [messageView.js](../web/static/js/services/messageView.js) | ✅ ⚠️ **veio grudado no commit do retornos (`d2a8b4c`)** |
| `filter.media.unknown` retirado do catálogo | [plugins/events.py:122-146](../plugins/events.py#L122) | ✅ o nome não tinha produtor; mantê-lo fazia plugin quebrado falhar em silêncio |
| Kit de teste de plugin publicado | [plugin_fixtures.py](../tests/plugin_fixtures.py), [plugin_test_utils.py](../tests/plugin_test_utils.py), [fake_provider.py](../tests/fake_provider.py) | ✅ **é a peça que torna esta poda possível** |

### 3.3 Falsos positivos descartados

| Alegação levantada na investigação | Por que NÃO é problema |
|---|---|
| "Vazamento de dados no WebSocket é bloqueador" | O defeito é real (todo operador logado recebe eventos de todas as conversas), mas é **anterior** a estas mudanças, já está publicado na `main` e já está documentado no [plano 90](90-plano-escopo-do-websocket-por-canal.md). Dívida conhecida, não impedimento desta release |
| "A separação de testes deixou o produto descoberto" | Exagero. Houve perda **pontual** de cobertura, não buraco |
| "Cherry-pick seletivo dos commits de core resolve" | Inviável: dos 70 commits, **15** tocam os plugins excluídos, há 4 merges (exigem `-m 1`) e dois monstros de reorganização (`1c7d13d` 267 arquivos, `440536b` 70) que geram conflito em cascata. Seriam ~57 cherry-picks para o mesmo resultado de árvore |
| "`assets/plugin_examples/protocolos/retornos_fields.py` quebra sem o `retornos`" | Não importa nada de `retornos` — o acoplamento é o filtro `filter.retornos.campos` publicado no bus. Sai junto com o `protocolos` de qualquer forma |
| "`trackify/phone.py:10-12` precisa de limpeza de telefone real" | Sai automaticamente junto com o plugin (F4). Um item a menos |

---

## 4. Mudanças de infraestrutura (testes)

Único refactor habilitador: **trocar provider real por sintético nos 5 call sites de módulo** (§2.3), usando [tests/fake_provider.py](../tests/fake_provider.py).

⚠️ Onde o teste exercita comportamento **específico** do provider (formatação de payload do Telegram em `:5123`/`:5334`, edição de mensagem, assinatura da Cloud), o `fake_provider` genérico **não substitui**. Nesses casos a decisão correta é **mover o teste para o repositório privado**, junto do plugin — é lá que ele passa a ter fonte. Decidir caso a caso na F2, registrando a escolha no bloco de status.

⚠️ [scripts/build_plugin_zips.py:44](../scripts/build_plugin_zips.py#L44) tem `DEFAULT_SOURCE_DIR = REPO_ROOT / "assets" / "plugin_examples"`. Com o espelho reduzido a `gowa`, o default fica quase sem fonte. Reapontar para o repositório externo ou documentar o `--source`.

---

## 5. Fases / Roadmap

```
WAVE 0  F0 · F1                                    ← higiene, independentes entre si
           │  (barreira: nada bloqueia, mas F0 é o item urgente)
WAVE 1  F2 · F3                                    ← preparar a poda [bloqueiam: F4]
           │  (barreira dura: a suíte precisa aguentar a remoção)
WAVE 2  F4                                         🔴 a poda [depende de: F2, F3]
           │
WAVE 3  F5 · F6                                    ← documentação [depende de: F4]
           │
WAVE 4  F7                                         🔴 suíte verde [depende de: tudo acima]
           │
WAVE 5  F8                                         🔴 merge developer → main
           │
WAVE 6  F9 · F10                                   ← redigir notas ‖ conferir repo
           │
WAVE 7  F11                                        🔴 publicar (rascunho → promover)
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | Retirar PII da árvore | 🟢 | baixo | `git ls-files \| grep plano-merge` vazio |
| 0 | **F1** | Limpar resíduos de identificador interno | 🟢 | baixo | varredura de `10.8.*` e nome de cliente limpa |
| 1 | **F2** | Migrar 5 call sites de módulo | 🟢 | **alto** | suíte verde **com** os plugins ainda presentes |
| 1 | **F3** | Mover 3 arquivos de teste para o repo privado | 🟢 | médio | testes rodam no runner externo |
| 2 | **F4** | Podar os 9 plugins | 🔴 | médio | `assets/plugin_examples/` = só `gowa` |
| 3 | **F5** | Corrigir `CLAUDE.md` | 🟢 | baixo | nenhum link para plugin removido |
| 3 | **F6** | Corrigir `README.md` | 🟢 | baixo | instruções executáveis por quem chega de fora |
| 4 | **F7** | Suíte verde no Postgres | 🔴 | médio | `pytest` verde |
| 5 | **F8** | Merge `developer` → `main` | 🔴 | baixo | merge trivial, sem exclusão |
| 6 | **F9** | Redigir as 6 notas de release | 🟢 | baixo | 6 textos revisados |
| 6 | **F10** | Conferir configuração do repositório | 🟢 | baixo | Immutable Releases verificado |
| 7 | **F11** | Publicar a escada | 🔴 | médio | 6 releases, `--latest` só na v0.7.0 |

---

### F0 — Retirar a PII da árvore 🟢

**Objetivo:** parar de publicar nome civil, telefone e histórico de 9 clientes reais, e o host do banco de produção.

**Itens:**
1. `[sequencial]` `git rm -r --cached plano-merge-contatos-duplicados` — 9 arquivos (4 `.md`, 4 `.sql`, 1 `README`), entrou em `bfb8648` (2026-08-05), commit cujo título era sobre o canal GOWA.
2. `[sequencial]` Acrescentar `plano-merge-contatos-duplicados/` ao [.gitignore](../.gitignore) **logo abaixo da linha 47**, que já bloqueia `plano-importacao-historico-chatwoot/` **pelo mesmo motivo** (dados de produção).
3. `[paralelo]` Preservar os arquivos localmente (o runbook é operacionalmente útil) — o `--cached` já faz isso.

⚠️ **Isto não desfaz o que já está público.** `developer` e `origin/developer` têm o mesmo hash; o conteúdo está no GitHub desde 05/08. Apagar de verdade é P1.

**Pronto quando:** `git ls-files | grep plano-merge-contatos-duplicados` não devolve nada, e o diretório continua existindo no disco.

#### Status de execução — Fase 0
**Estado:** ✅ Concluída (2026-08-07)
- **O que foi feito:** `git rm -r --cached plano-merge-contatos-duplicados` (9 arquivos destrackeados, preservados em disco) e nova entrada em [.gitignore](../.gitignore) logo abaixo da linha do `plano-importacao-historico-chatwoot/`, com comentário explicando o motivo (nome civil + telefone de clientes reais, host de produção, repositório público).
- **Como foi feito / decisões:** `--cached` em vez de `git rm` puro — o runbook continua operacionalmente útil na máquina do dev. O comentário no `.gitignore` foi escrito explicando **por quê**, e não só o quê, para que a próxima pessoa não "limpe" a regra achando que é resquício.
- **Problemas / pendências:** nenhum. ⚠️ Continua valendo o R1: o conteúdo está em `origin/developer` desde 05/08 e só sai de verdade com a reescrita de histórico (P1).
- **Verificação:** `git ls-files | grep plano-merge-contatos-duplicados` → vazio; `ls plano-merge-contatos-duplicados/` → os 5 itens ainda presentes em disco.

---

### F1 — Limpar resíduos de identificador interno 🟢

**Objetivo:** quem sanear olhando só a pasta da F0 deixa rastro. Estes são os que sobram no que **vai** para a `main`.

**Itens:** (todos `[paralelo]`)

| Arquivo | Linha(s) | Conteúdo | Ação |
|---|---|---|---|
| [docs-planos/90-plano-escopo-do-websocket-por-canal.md](90-plano-escopo-do-websocket-por-canal.md) | 4 | `10.8.100.5` | genericizar |
| [docs-planos/83-plano-extrair-plugins-do-core.md](83-plano-extrair-plugins-do-core.md) | 465 | nome comercial do cliente — **reverte a genericização do plano 78** | genericizar |
| [tests/core/legacy/legacy_endpoints.py](../tests/core/legacy/legacy_endpoints.py) | 5861, 5867, 5870, 5873 | `10.8.200.4`, anotado como "a cadeia real da instância" | trocar por IP de documentação (RFC 5737) |

⚠️ São **4 ocorrências** em `legacy_endpoints.py`, não 1 — o teste monta a cadeia XFF em vários pontos. Trocar todas de uma vez e conferir que o teste continua exercitando hop privado.

✅ O resíduo de `assets/plugin_examples/trackify/phone.py:10-12` (telefone real + estatística da base do cliente) **sai sozinho na F4**.

**Pronto quando:** `grep -rnoE '\b10\.8\.[0-9]+\.[0-9]+\b'` fora de `venv/` não devolve nada, e o nome do cliente não aparece em `docs-planos/`.

#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-08-07)
- **O que foi feito:** os 3 itens previstos, **mais 3 resíduos que o plano não tinha mapeado**:
  1. `docs-planos/90:4` — `whatsbot@10.8.100.5` → "numa instância de produção real".
  2. `docs-planos/83:465` — "a instância `<nome do cliente>`" → "uma instância real".
  3. `legacy_endpoints.py` (4 linhas) — `10.8.200.4` → `10.99.0.4`, e o comentário "a cadeia real da instância" → "cadeia de exemplo".
  4. **(novo)** `docs-planos/90:84-90` — a tabela "Dimensão real do vazamento" trazia nomes de canal de produção e o **primeiro nome de uma operadora real** em duas linhas. Genericizada preservando os números.
  5. **(novo)** 10 menções a um SaaS de terceiro nomeado (`Curseduca`) em [conversation_repo.py:363,388](../db/repositories/conversation_repo.py#L363), [test_channel_default_assignee.py](../tests/integration/test_channel_default_assignee.py) (incl. o nome de uma função de teste) e [AiSettingsFields.js:46](../web/static/js/components/channels/AiSettingsFields.js#L46) → "plataforma externa" / "integração externa".
- **Como foi feito / decisões:**
  - ⚠️ **Desvio deliberado do plano:** o item 3 mandava trocar por endereço RFC 5737. **Estaria errado.** As faixas de documentação (`192.0.2/24`, `198.51.100/24`, `203.0.113/24`) são de escopo **global**, e o teste depende de o hop ser **privado** — é ele que exercita a cadeia toda-confiável caindo no mais à esquerda ([client_ip.py:54](../server/client_ip.py#L54)). Prova de que os dois casos são distintos: a linha 5877, logo abaixo, usa `203.0.113.9` **de propósito** para o caso oposto. Trocado por `10.99.0.4`, privado e obviamente sintético.
  - O nome do produto de terceiro (item 5) foi genericizado por ser identidade de negócio de um cliente num repositório público, e o custo era baixo (10 pontos).
- **Problemas / pendências:** ⏸️ **"Nexus" fica** — ~15 menções ("portado do nexus", "estilo Nexus") em código, migration e docstrings. É **atribuição de design**, não identidade de cliente nem PII, e reescrever espalharia ruído por 8 arquivos. Registrado aqui como decisão consciente, não como esquecimento. Se a preferência for remover, é um `sed` isolado.
- **Verificação:** `git grep -nE '\b10\.8\.[0-9]+\.[0-9]+\b'` fora de `venv/` → vazio. `git grep -rni 'curseduca'` → vazio. `py_compile` OK nos 2 arquivos Python; `node --check` OK no JS. Os demais IPs privados encontrados (`10.0.0.9`, `192.168.0.7`) são valores sintéticos de teste e a lista de CIDR em [client_ip.py:54](../server/client_ip.py#L54) é funcional — mantidos.

---

### F2 — Migrar os 5 call sites de módulo 🟢 `[bloqueia: F4]`

**Objetivo:** fazer a suíte legada sobreviver à ausência dos plugins — **antes** de removê-los.

**Itens:**
1. `[sequencial]` Para cada linha da tabela §2.3, decidir entre **(a)** substituir por [tests/fake_provider.py](../tests/fake_provider.py) quando o teste exercita contrato genérico, ou **(b)** mover o teste para o repositório privado quando exercita comportamento específico do provider.
2. `[paralelo]` `:683` (`whatsapp_cloud`) e `:6591` — provável (a): o teste cobre o contrato de canal.
3. `[paralelo]` `:690`, `:5123`, `:5334` (`telegram`) — provável (b): formatação de payload e edição de mensagem são específicas do Telegram.
4. `[paralelo]` `:2558` (`protocolos`) — cobre Kanban Views (`:2551-2810`) e RBAC (`:3056-3067`). Avaliar mover a seção inteira.
5. `[sequencial]` `:6839` (`plugin_dir = … / prov`, dinâmico, dentro de função) — verificar quais `prov` são exercitados.

⚠️ **Critério de saída rigoroso:** a suíte precisa ficar **verde com os plugins ainda presentes**. Se F2 e F4 forem feitas juntas, um vermelho fica ambíguo entre "migração errada" e "faltou arquivo".

**Pronto quando:** `venv/bin/python -m pytest` verde, `assets/plugin_examples/` ainda intacto, e `grep -c 'assets/plugin_examples' tests/core/legacy/legacy_endpoints.py` só devolve ocorrências de `gowa` ou comentário.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-08-07)
- **O que foi feito:** ⚠️ **O escopo real era MAIOR que o mapeado.** Não eram 5 call sites, e sim **7 regiões (~1.400 linhas)** em `legacy_endpoints.py`, mais **11 outros arquivos** de teste que pedem plugin por outro caminho. Detalhe:
  1. Guardadas 7 regiões da suíte legada com o helper novo `plugin_source_or_skip` (definido junto de `check`/`section`): capabilities revoke/edit (`:683-695`), Kanban do `protocolos` (`:2552-3417`, 865 linhas), seção 19c do Telegram (`:5120-5253`), parse de `edited_message` (`:5333-5347`), templates + upload + dedup (`:6587-6947`) e mascaramento de credencial do plano 76.
  2. O loader genérico `_p32_load_provider` foi **içado para fora** do guard — a região do plano 76, ~200 linhas adiante, o usa.
  3. O sumário passou a imprimir `N blocks skipped` e a listar quais.
  4. **Costura de origem** (o que de fato resolveu os outros 11 arquivos): `build_test_app` ([tests/support.py](../tests/support.py)) e `load_plugin_package` ([tests/plugin_test_utils.py](../tests/plugin_test_utils.py)) passaram a **`pytest.skip`** quando a fonte não existe. `gowa` ausente continua falha dura — esse é bundled.
- **Como foi feito / decisões:**
  - ⚠️ **Descartei as duas opções do plano** ((a) `fake_provider`, (b) mover para o privado) para estas regiões: elas asseram comportamento **específico do provider** (o `fake_provider` não substitui) e estão **coladas no estado compartilhado** de um script linear de 7.174 linhas (mover = reescrever, com risco alto de perder asserção em silêncio). Conferi antes: a cobertura **não** está duplicada no repo privado (o `telegram` lá só cobre `parse_inbound` de citação; o `whatsapp_cloud` não cobre capabilities). Apagar perderia asserções de verdade.
  - Preferi consertar **na origem** (2 funções) a marcar 11 arquivos um a um. O princípio: *a suíte do núcleo não pode ficar vermelha por faltar um plugin que o núcleo deliberadamente não distribui.*
  - Indentação mecânica validada por três provas independentes: `py_compile`, contagem de `check(` idêntica antes/depois (**1607**), e o resultado de execução idêntico.
- **Problemas / pendências:** 🔴 **Armadilha grave descoberta e contornada** — o resolvedor cai em `storages/plugins/<id>`, e esta máquina tem 6 dos 9 plugins **instalados** ali. A suíte ficaria **verde aqui e vermelha num clone limpo**. Por isso a F7 roda num **worktree limpo**, não neste checkout. Documentado no [CLAUDE.md](../CLAUDE.md).
- **Verificação:** com os plugins presentes, `RESULTS: 1640 passed, 0 failed, 0 blocks skipped` — **idêntico à linha de base** medida antes de qualquer mudança. Sem eles, `1337 passed, 0 failed, 9 blocks skipped` (303 checks pulados, nenhum quebrado).

---

### F3 — Mover 3 arquivos de teste para o repositório privado 🟢 `[bloqueia: F4]`

**Objetivo:** os testes seguem o plugin.

**Itens:**
1. `[paralelo]` [tests/integration/test_trackify_plugin.py](../tests/integration/test_trackify_plugin.py) (2.657 linhas) → `../whatsbot-pro-plugins/plugins/trackify/tests/`. **Hoje o `trackify` é o único plugin do repo privado sem pasta `tests/`.** Trocar o `_SRC` fixo de [:27](../tests/integration/test_trackify_plugin.py#L27) pelo `resolve_plugin_source("trackify")`, que já procura `WHATSBOT_PLUGIN_SOURCE_ROOT/<id>/src` primeiro ([plugin_test_utils.py:42](../tests/plugin_test_utils.py#L42)) — exatamente o que o runner `scripts/test_plugins.py` injeta.
2. `[paralelo]` Teste de `facebook_messenger` → repo privado.
3. `[paralelo]` Teste de `website` → repo privado.
4. `[sequencial]` Decidir o destino de [tests/support_js/smoke_plugin_screen.mjs](../tests/support_js/smoke_plugin_screen.mjs) e `lint_phantom_setters.mjs` — o único chamador era o teste do trackify. São **genéricas**; recomendação: copiar para `whatsbot-pro-plugins/scripts/` e **manter** a cópia no core.

**Pronto quando:** `cd ../whatsbot-pro-plugins && python3 scripts/test_plugins.py trackify` verde.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída (2026-08-07)
- **O que foi feito:** `tests/integration/test_trackify_plugin.py` (2.657 linhas) foi para `whatsbot-pro-plugins/plugins/trackify/tests/python/` — o `trackify` era o único plugin do repo privado **sem** pasta `tests/`, e agora tem. Duas âncoras trocadas: `_SRC` fixo → `resolve_plugin_source("trackify")` (que o `conftest.py` de lá já alimenta via `WHATSBOT_PLUGIN_SOURCE_ROOT`), e `_SUPORTE_JS` → `scripts/js/` na raiz daquele repo, para onde `smoke_plugin_screen.mjs` e `lint_phantom_setters.mjs` foram **copiados** (o núcleo mantém a própria cópia — são genéricos).
- **Como foi feito / decisões:** **Os outros 2 movimentos previstos não existiam.** O plano supunha arquivos de teste próprios para `facebook_messenger` e `website`; a varredura mostrou que ambos só apareciam **dentro** de `legacy_endpoints.py`, já coberto pela F2. Um item a menos, não um item esquecido.
- **Problemas / pendências:** o repo privado ainda **não foi commitado** — os arquivos estão em disco lá, aguardando junto do resto da aprovação de push.
- **Verificação:** `parents[4]` confere com a raiz do repositório de plugins; o arquivo saiu do núcleo por `git rm` e as 192 falhas que ele produzia no clone limpo desapareceram.

---

### F4 — Podar os 9 plugins 🔴 `[depende de: F2, F3]`

**Objetivo:** `assets/plugin_examples/` fica com `gowa` e mais nada.

**Itens:**
1. `[sequencial]` **Conferência obrigatória antes de apagar** — para cada um dos nove:
   ```
   diff -rq --exclude=__pycache__ --exclude='*.pyc' \
     assets/plugin_examples/<id> ../whatsbot-pro-plugins/plugins/<id>/src
   ```
   Só `.test.js` renomeado e versão do privado **maior ou igual** são aceitáveis. Qualquer arquivo `Only in assets/` que não seja teste ⇒ **PARE** e reconcilie primeiro.
2. `[sequencial]` `git rm -r assets/plugin_examples/{telegram,whatsapp_cloud,facebook_messenger,instagram,website,protocolos,melhorias,retornos,trackify}`
3. `[sequencial]` Reapontar ou documentar `DEFAULT_SOURCE_DIR` em [scripts/build_plugin_zips.py:44](../scripts/build_plugin_zips.py#L44).

⚠️ **Não** confiar em número de versão para julgar paridade. O histórico deste repositório registra duas cópias distintas do `protocolos` marcadas com a **mesma** versão. Compare **conteúdo**.

**Pronto quando:** `ls assets/plugin_examples/` devolve só `gowa`, e `git status` mostra 178 arquivos removidos.

#### Status de execução — Fase 4
**Estado:** ✅ Concluída (2026-08-07)
- **O que foi feito:** os 9 plugins saíram — **178 arquivos**, exatamente o número previsto. `assets/plugin_examples/` ficou só com `gowa`. `DEFAULT_SOURCE_DIR` em [scripts/build_plugin_zips.py](../scripts/build_plugin_zips.py) ganhou o comentário apontando o `--source` para o repositório de plugins.
- **Como foi feito / decisões:** conferência `diff -rq` **um a um** antes de apagar. Resultado:

  | Plugin | Veredito |
  |---|---|
  | `telegram`, `facebook_messenger`, `instagram`, `website`, `retornos`, `trackify` | idênticos, byte a byte |
  | `whatsapp_cloud` | privado **1.10.3** > espelho 1.10.2 |
  | `melhorias` | privado **1.7.1** > espelho 1.7.0 |
  | `protocolos` | privado **1.28.0** > espelho 1.25.0 |

  Todo arquivo `Only in assets/` era um `.test.js` que o privado guarda em `tests/js/`. Conferi os cinco: `phone.test.js`, `proto_fields.test.js`, `chat_core.test.js` e `markdown.test.js` estão lá com o mesmo nome; `templateFilter.test.js` está como `template_filter.test.js` — **19 testes dos dois lados**, `diff` acusando só o comentário de caminho. Nada se perdeu.
- **Problemas / pendências:** ⚠️ **Achado que o plano não previa:** o `whatsapp_cloud` do espelho tinha um `phone.test.js` além do `templateFilter.test.js` documentado. Também existe no privado — mas é a segunda vez que a conferência acha um arquivo a mais do que o inventário dizia, o que reforça a regra do F4·1: `diff` sempre, nunca confiar na lista.
- **Verificação:** `ls assets/plugin_examples/` → só `gowa`; `git status` → 178 remoções; a composição por plugin bate com a tabela §3.1.

---

### F5 — Corrigir o `CLAUDE.md` 🟢 `[depende de: F4]`

**Objetivo:** o `CLAUDE.md` público parar de descrever código ausente.

**Itens:**
1. `[sequencial]` Apagar a seção `### Plugin \`retornos\`` — [CLAUDE.md:819](../CLAUDE.md#L819) em diante, ~15 linhas densas com ~40 links para `assets/plugin_examples/retornos/*`.
   ⚠️ Essa seção **já mente hoje**: a última linha lista quatro testes do `retornos` que não existem no `developer`. É a evidência de que ela não se mantém sozinha.
2. `[paralelo]` Revisar toda menção a `protocolos`, `melhorias`, `trackify` e aos 5 providers — trocar link de arquivo por nota de uma linha ("distribuído pela loja de plugins").
3. `[paralelo]` Generalizar os 2 comentários do frontend que citam plugin por nome: [messageView.js:117](../web/static/js/services/messageView.js#L117), [ContactDetail.js:851](../web/static/js/components/contacts/ContactDetail.js#L851).
4. `[paralelo]` Atualizar a tabela "Onde vive o código de um plugin" — `assets/plugin_examples/` deixa de ser espelho de 10 plugins e passa a ser **só o bundled `gowa`**.

**Pronto quando:** nenhum link de `CLAUDE.md` aponta para `assets/plugin_examples/<id>/` com `<id> != gowa`.

#### Status de execução — Fase 5
**Estado:** ✅ Concluída (2026-08-07)
- **O que foi feito:** removida a seção `### Plugin retornos` (16 linhas); **17 links** markdown para plugins que saíram convertidos em code span (o texto continua nomeando o arquivo, sem link morto); 5 afirmações em prosa que passaram a mentir foram reescritas (bundling, bootstrap, o parágrafo do "espelho transitório", a linha 2 da tabela "Onde vive o código" e a nota do resolvedor); os 2 comentários de frontend generalizados.
- **Como foi feito / decisões:** acrescentei ao `CLAUDE.md` o aviso da armadilha que me custou uma rodada inteira de suíte: **o resolvedor cai em `storages/plugins/`**, então uma máquina de desenvolvimento com o plugin instalado fica verde enquanto um clone limpo fica vermelho. Sem esse aviso, a próxima pessoa cai no mesmo buraco.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `grep` por `assets/plugin_examples/<id>` com `<id> != gowa` → zero.

---

### F6 — Corrigir o `README.md` 🟢 `[depende de: F4]`

**Objetivo:** quem chega de fora conseguir rodar o produto. **O README não foi tocado em 70 commits.**

**Itens:**

| Linha | Diz hoje | Realidade |
|---|---|---|
| [:30](../README.md#L30) | "dois cliques em **start.bat**" | ❌ `start.bat` **não existe**. O real é `windows_start.bat` |
| [:193](../README.md#L193) | "dois cliques em **start.command**" | ❌ não existe. O real é `macos_start.command` |
| [:239](../README.md#L239) | idem | ❌ idem |
| — | **não menciona PostgreSQL nem `DATABASE_URL`** | ❌ o produto **não sobe sem eles** — falha explícita no boot |

1. `[paralelo]` Corrigir os 3 nomes de launcher.
2. `[sequencial]` Acrescentar seção de pré-requisito: PostgreSQL + `DATABASE_URL` obrigatória, com exemplo `postgresql+psycopg://usuario:senha@host:5432/whatsbot`.
3. `[sequencial]` Declarar o modelo de distribuição: **core + canal GOWA**; demais canais e extensões vêm da loja por `Importar (.zip)`.
4. `[paralelo]` Ajustar o convite a "copiar e modificar" enquanto não houver `LICENSE` (ver P2).

**Pronto quando:** um leitor externo consegue subir o produto seguindo o README literalmente.

#### Status de execução — Fase 6
**Estado:** ✅ Concluída (2026-08-07)
- **O que foi feito:** os 3 nomes de launcher corrigidos — **e um quarto que o plano não tinha achado**: a FAQ mandava "abrir o **iniciar.bat** de novo", um nome que nunca existiu em lugar nenhum. Acrescentada a seção **"Um banco PostgreSQL (obrigatório em todas as opções)"** com o exemplo de `DATABASE_URL` e a observação de que serve tanto local quanto hospedado, e cada uma das 3 opções de instalação ganhou o pré-requisito na lista. Declarado o modelo de distribuição na seção de plugins: núcleo + canal WhatsApp, o resto pela loja.
- **Como foi feito / decisões:** ⏸️ **Não mexi na seção Licença.** Ela diz hoje "Projeto de código aberto. Livre para uso pessoal e comercial." — que é uma concessão em prosa, sem arquivo `LICENSE`. Trocar isso é decisão jurídica do dono do projeto (P2), e qualquer redação que eu escolhesse seria mais restritiva **ou** mais permissiva do que ele pretende. Fica sinalizado, não decidido.
- **Problemas / pendências:** P2 (licença) continua aberta e **deveria ser resolvida antes da F11** — o README convida a copiar e modificar, e sem `LICENSE` o padrão jurídico é o oposto disso.
- **Verificação:** `grep` ancorado por `(^|[^_a-z])(start\.bat|start\.command|iniciar\.bat)` → zero; os 4 launchers citados existem em disco.

---

### F7 — Suíte verde no Postgres 🔴

**Objetivo:** provar que a poda não quebrou nada, antes de tocar na `main`.

**Itens:**
1. `[sequencial]` `venv/bin/python -m pytest` — coleta limitada a `tests/core`, `tests/contracts`, `tests/integration` (`pyproject.toml`).
2. `[sequencial]` `cd ../whatsbot-pro-plugins && python3 scripts/test_plugins.py --all`.
3. `[paralelo]` `node --test` nos módulos puros do core.

⚠️ **Nunca rodar duas suítes Postgres em paralelo** — cada processo recria o mesmo schema `public`, e o concorrente pode estar em outra máquina. Conferir `pg_stat_activity` antes de culpar o código.

⚠️ Vermelho em `tests/core/test_legacy_scripts.py` = a F2 não cobriu algum call site de módulo. **Não** prosseguir.

**Pronto quando:** as três verdes, sem falha inexplicada.

#### Status de execução — Fase 7
**Estado:** ✅ Concluída (2026-08-07) — **paridade provada com controle**
- **O que foi feito:** a suíte rodou num **worktree limpo** (`git worktree add --detach`, sem `storages/`), que é a única forma honesta de medir: neste checkout o resolvedor cairia em `storages/plugins/` e mascararia tudo. Depois rodei o **mesmo experimento no commit `1f11809`** (antes de qualquer mudança minha) como CONTROLE.

  | | Controle (pré-limpeza) | Pós-limpeza |
  |---|---|---|
  | Falhas na suíte completa | **4** | **4** |
  | Conjunto | idêntico | idêntico |

- **Como foi feito / decisões:** ⚠️ **Duas medições intermediárias foram descartadas por serem inválidas, não por serem ruins.**
  1. **63 falhas** numa rodada — causa: `relation "plugins" does not exist`. Duas execuções concorrentes que eu havia matado deixaram o schema `public` pela metade (48 tabelas inconsistentes). Reset do schema → caiu para 6.
  2. **6 falhas** na seguinte — 2 a mais que o controle (`legacy_endpoints`, `legacy_agent_json_hardening`). **Repeti a rodada com o mesmo commit e schema resetado: as 2 sumiram.** Eram corrida, não regressão. O mecanismo está nas tracebacks: esses scripts legados rodam como subprocesso e dão `DROP SCHEMA public CASCADE` para virar donos exclusivos do banco, enquanto a sessão-mãe do pytest usa o mesmo banco — `legacy_endpoints` recebeu corpo vazio de `/api/roles`, `legacy_agent_json_hardening` bateu numa tabela ausente. Ambos passam **isolados** no commit pós-limpeza: **1337/0** e **25/0**.
  - Lição para quem executar isto de novo: **um número de falhas só vale contra um controle no commit anterior**. Sem controle eu teria reportado três vezes um resultado errado.
- **Problemas / pendências:** 🔵 **As 4 falhas remanescentes são PRÉ-EXISTENTES e já estão na `main`.** Não foram consertadas — estão fora do escopo deste plano, e cada uma merece o seu:
  1. `test_alembic_hygiene` (2) — revisão de merge `0058_merge_p50_p57` quebra a cadeia linear, e há 5 prefixos de sequência duplicados (`0037`, `0042`, `0043`, `0046`, `0052`).
  2. `test_audit_matrix_is_complete` — a matriz de caracterização está **9 eventos atrás** de `AUDITABLE_EVENTS` (`channel.created/updated/deleted/restored/session_action/members_changed/duplicate_refused`, `plugin.imported/deleted`). Alguém acrescentou os eventos e não atualizou a matriz.
  3. `legacy_gowa_plugin_lifecycle` — `reconcile: spawns exactly the valid proxied channel`. ⚠️ **Este é do plugin que FICA** (`gowa`, proxy por número — plano 52). Confirmado reprovando **isolado** nos dois commits.
  - ⚠️ Também vale registrar: a suíte tem uma **fragilidade estrutural** — os scripts legados exigem ser donos exclusivos do banco e derrubam o schema no meio da sessão do pytest. Funciona por acidente de ordenação. Qualquer mudança que altere a duração dos testes (como esta poda, que troca construções de app por skips instantâneos) pode fazê-la aparecer.
- **Verificação:** conjuntos de falha idênticos entre controle e pós-limpeza (`diff` dos dois arquivos de saída). `legacy_endpoints` isolado: `RESULTS: 1337 passed, 0 failed, 9 blocks skipped`.
  **Repositório de plugins** (`scripts/test_plugins.py --all`): **13 plugins, ~969 testes Python + 149 JS, ZERO falhas** — incluindo `trackify`, cujo teste migrou na F3 e agora roda na casa dele.

---

### F8 — Merge `developer` → `main` 🔴

**Objetivo:** merge **trivial**, sem lista de exclusão.

**Itens:**
1. `[sequencial]` Commit e push da limpeza no `developer`.
2. `[sequencial]` `git checkout main && git merge developer`
3. `[sequencial]` `venv/bin/python -m pytest` na `main`.
4. `[sequencial]` `git push origin main`.

⚠️ **Não** usar `--no-commit`, **não** fazer `git rm` depois. Depois de F0–F4 os dois branches concordam sobre o que existe, e este e **todos os merges seguintes** voltam a ser merges comuns. Ver §6·R7.

**Pronto quando:** `git diff main developer` vazio.

#### Status de execução — Fase 8
**Estado:** ✅ Concluída (2026-08-07) — `origin/main` em `76a732b`
- **O que foi feito:** `developer` → `main`, merge comum, sem lista de exclusão e sem `git rm` posterior, como o plano previa. `origin/main` saiu de `2bb942e` para `76a732b`.
- **Como foi feito / decisões:**
  - ⚠️ **O plano não previu um segundo autor.** Entre a publicação da branch de teste e o merge, a Luisa publicou `f0e9473` no `origin/developer` (menu **⋮** do cabeçalho da conversa + engrenagem na sidebar). O meu `developer` local havia divergido: **push rejeitado por não-fast-forward**. Integrado com **merge de verdade** (`d8873be`) — nunca `rebase`, que reescreveria commits já publicados na `release/instalacao-limpa`.
  - **Conflito `modify/delete` em `assets/plugin_examples/protocolos/static/`** (`extends.js`, `resolve_form.js`): o commit dela editava dois arquivos que a F4 apagou. Resolvido **a favor da poda** — o espelho do `protocolos` não volta. O trabalho dela **não se perdeu**: o patch foi extraído e portado para `whatsbot-pro-plugins/plugins/protocolos/src/static/` (ver "Problemas / pendências").
  - Ao portar, apareceu uma divergência real entre o espelho e a fonte publicada: a 1.28.0 já busca o protocolo dentro de `protoShortcut`, mas **só quando existe algum campo marcado "Mostrar ao resolver"**. Reaproveitar cegamente esse valor acoplaria o atalho a uma configuração que não tem nada a ver com ele, então o porte reaproveita quando existe e busca por conta própria quando não.
  - **3 referências mortas** a `assets/plugin_examples/<id>` sobreviveram à F5/F6 (2 em [docs/PLUGINS_AUDITAVEIS.md](../docs/PLUGINS_AUDITAVEIS.md), 1 num comentário de [TemplatePicker.js](../web/static/js/components/contacts/TemplatePicker.js)). Corrigidas em `9b59b49` — apontam para `storages/plugins/<id>`, e só o `gowa` mantém o link para `assets/`.
- **Problemas / pendências:** o porte do atalho "Ver protocolo" está **na árvore de `whatsbot-pro-plugins`, não commitado** — junto com o teste do `trackify` (F3) e `scripts/js/`. Aguarda decisão do dono do projeto, porque mexe num plugin que roda em produção.
- **Verificação:** `git diff main developer` **vazio** (critério de "Pronto quando"). `assets/plugin_examples/` só com `gowa`; **0** arquivos de PII rastreados; nenhuma referência a caminho podado fora de `docs-planos/`. Sintaxe dos 9 arquivos JS do merge conferida com `node --input-type=module --check`.
  **Suítes:** frontend `node --test` **538/538**. Core `pytest`: **654 testes coletados em 79 arquivos, 3 falhas, 4 skips** — `test_linear_chain_single_parent_reaches_all_revisions`, `test_no_unexpected_duplicate_sequence_prefixes` e `test_audit_matrix_is_complete`, as três já catalogadas na F7 como pré-existentes. O controle da F7 tinha **4**; a quarta (`legacy_gowa_plugin_lifecycle`) passou nesta rodada, coerente com a fragilidade de ordenação já registrada. **Nenhuma falha nova.**

---

### F9 — Redigir as 6 notas de release 🟢

**Objetivo:** seis textos em PT-BR, linguagem de produto, para quem **usa** o WhatsBot.

**A escada** — os 5 primeiros alvos foram verificados como ancestrais de `main` (`git merge-base --is-ancestor`):

| Versão | Título | Alvo | Data | Commits | Tipo |
|---|---|---|---|---:|---|
| **v0.2.0** | A plataforma de atendimento | `2cfbbb0` (merge PR #1) | 29/06 | 203 | retroativa |
| **v0.3.0** | ⚠️ Só PostgreSQL, e a IA multiagente com freio | `a4e8ac1` | 02/07 | 68 | retroativa |
| **v0.4.0** | Cada canal no seu lugar | `6c4dba4` (merge PR #2) | 14/07 | 167 | retroativa |
| **v0.5.0** | ⚠️ Login só por identidade e um IP por número | `6e19290` | 16/07 | 41 | retroativa |
| **v0.6.0** | Escala, mídia e canais novos | `2bb942e` (HEAD `main` antigo) | 28/07 | 124 | retroativa |
| **v0.7.0** | Conversa sob controle | `1f11809` | 06/08 | 70 | **nova** |

**Por que 6 e não 3:** os degraus v0.3.0 e v0.5.0 existem para **isolar as duas quebras**. Numa nota única, `DATABASE_URL` obrigatória e o fim da Senha do Painel cairiam no mesmo texto que outros dois checklists — e o leitor pula.

**Itens:**
1. `[paralelo]` Redigir as 6 notas.
2. `[sequencial]` ⚠️ **Ajustar pela poda (D3):** os degraus v0.4.0 (widget de site), v0.6.0 (Messenger, Instagram) e v0.7.0 (plugin `retornos`) anunciam canais e extensões cujo código **não está** no repositório. Cada um precisa dizer que vem da **loja de plugins**, por `Importar (.zip)`.
3. `[sequencial]` ⚠️ **Declarar na v0.7.0:** a tela nativa de Atendimentos foi removida (~932 linhas) e quem a fornece é o `protocolos`, agora da loja. Sem ele, a rota redireciona para `/contacts` ([ScreenRouter.js:159](../web/static/js/components/shell/ScreenRouter.js#L159)).
4. `[paralelo]` Incluir as ações de atualização de cada degrau (migrations, ordem de deploy, permissão sem dono, mudança em `/assign`).

**Pronto quando:** 6 textos revisados, cada um com sua seção de "ações de atualização".

#### Status de execução — Fase 9
**Estado:** ✅ Concluída (2026-08-07) — **textos prontos, aguardando revisão do dono do projeto**
- **O que foi feito:** as 6 notas redigidas, uma por arquivo, em `<scratchpad>/releases/v0.{2..7}.0.md`. Cada uma tem seção **"Ações de atualização"**; as duas que quebram (v0.3.0 e v0.5.0) abrem com um bloco de aviso antes de qualquer outra coisa.
- **Como foi feito / decisões:**
  - Linguagem de **produto**, para quem usa o WhatsBot — não changelog de commit. Onde a mudança conserta um defeito que o usuário sentiu, a nota **conta o defeito** (a resposta da IA saindo 75s depois do clique; o salto de mensagem que falhava em silêncio; o gerenciador de senhas preenchendo o campo do token com a senha do painel; os 118 grupos materializados).
  - **D3 aplicado** nas três notas que anunciam plugin: v0.4.0 (widget/melhorias), v0.6.0 (Instagram/Messenger) e v0.7.0 (protocolos/retornos) dizem explicitamente que o recurso vem da **loja**, e a v0.7.0 avisa que `/atendimentos` degrada para a lista de conversas sem o plugin instalado.
  - A v0.6.0 ganhou um aviso que o plano não previa: **aplicar as migrações antes** de subir a versão, por causa dos índices de busca — quem tem base grande sente na hora.
- **Problemas / pendências:** os textos **não** foram publicados nem enviados a lugar nenhum. A F11 depende de revisão.
- **Verificação:** conteúdo conferido contra `git log` de cada intervalo; os 5 alvos retroativos já haviam sido confirmados como ancestrais de `main`.

---

### F10 — Conferir a configuração do repositório 🟢

**Itens:**
1. `[paralelo]` Verificar se **Immutable Releases** está habilitado. Se estiver, depois de publicar não dá para alterar nem apagar tag/assets — só rascunho é editável.
2. `[paralelo]` Confirmar que `git ls-remote --tags origin` continua vazio (nada para contradizer a numeração).
3. `[paralelo]` ⚠️ Corrigir ou ignorar [.claude/commands/release-up.md](../.claude/commands/release-up.md): manda dar push em `origin` **e** `upstream`, e o remote `upstream` **não existe mais** neste checkout. Além disso, o passo 1 usa `git describe --tags`, que hoje acha as tags **órfãs**.

**Pronto quando:** as três confirmadas.

#### Status de execução — Fase 10
**Estado:** ✅ Concluída (2026-08-07)
- **O que foi feito:** os 3 itens.
  1. **Immutable Releases:** `gh api repos/:owner/:repo/rulesets` devolve `[]` e o payload do repositório não expõe campo de imutabilidade — nada travando. Publicar e corrigir continua possível.
  2. `gh release list` e `git ls-remote --tags origin` **ambos vazios** — a numeração da escada segue livre.
  3. [.claude/commands/release-up.md](../.claude/commands/release-up.md) **reescrito**. Tinha três defeitos: mandava dar push num remote `upstream` que **não existe** (`git remote -v` só tem `origin`), descobria a versão por `git describe --tags` (que acha as tags **órfãs** e mente), e usava `--latest` sem nunca mencionar o `--latest=false` das retroativas. Agora começa por `gh release list`, publica como rascunho primeiro e explica por que `--generate-notes` é inútil aqui.
- **Como foi feito / decisões:** aproveitei para escrever no próprio comando o **motivo** de cada regra, não só a regra — é o arquivo que a próxima release vai seguir.
- **Problemas / pendências:** nenhuma.
- **Verificação:** os três confirmados por chamada real ao `gh`/`git`.

---

### F11 — Publicar a escada 🔴

**Objetivo:** 6 releases publicadas sem inundar quem acompanha o repositório.

**Itens:**
1. `[sequencial]` Criar as 6 como **rascunho** (`--draft`). Rascunho **não notifica** e **não cria a tag remota** — é a rede de segurança.
2. `[sequencial]` Revisar os 6 rascunhos no GitHub.
3. `[sequencial]` Promover em **ordem crescente** (v0.2.0 → v0.7.0), do commit mais antigo para o mais novo.
4. `[sequencial]` ⚠️ **`--latest=false` explícito nas 5 retroativas.** O `gh` só envia `make_latest` quando `--latest` é passado; omitido, vale o default da API REST, que é `true` — publicar a v0.3.0 (commit de junho) sem o flag marcaria uma release antiga como a atual.
5. `[sequencial]` `--latest` explícito **só na v0.7.0**.

✅ **A ordem da LISTA resolve sozinha:** o GitHub ordena por data do **commit**, não da publicação. As retroativas caem no lugar cronológico certo.

⚠️ **`--generate-notes` é inútil aqui** — o GitHub monta a nota a partir de *pull requests*, e o repositório tem **2 PRs no total**. Todas as notas são escritas à mão (F9).

**Pronto quando:** `gh release list` mostra 6, só a v0.7.0 marcada como Latest.

#### Status de execução — Fase 11
**Estado:** ✅ Concluída (2026-08-07) — 6 releases no ar, só a v0.7.0 como *Latest*
- **O que foi feito:** as 6 criadas como rascunho, conferidas e promovidas em ordem crescente. `gh release list` devolve 6; `releases/latest` devolve `v0.7.0`.

| Versão | Tag aponta para | Data do commit | Latest |
|---|---|---|---|
| v0.2.0 | `2cfbbb0` | 29/06 | não |
| v0.3.0 | `a4e8ac1` | 02/07 | não |
| v0.4.0 | `6c4dba4` | 14/07 | não |
| v0.5.0 | `6e19290` | 16/07 | não |
| v0.6.0 | `2bb942e` | 28/07 | não |
| **v0.7.0** | **`76a732b`** | **07/08** | **sim** |

- **Como foi feito / decisões:**
  - ⚠️ **O alvo da v0.7.0 mudou: `76a732b` (HEAD da `main`), NÃO o `1f11809` da tabela da F9.** Medido antes de publicar: `1f11809` ainda carrega **9 arquivos de PII** e os **10 espelhos de plugin**. Uma release cria uma tag e um **tarball baixável**; apontá-la ali publicaria, num repositório **PÚBLICO** e num artefato de primeira classe, exatamente o que a F0 removeu — e ainda entregaria ao cliente um zip que contradiz a própria nota ("distribuído enxuto"). Os **5 alvos retroativos foram medidos e estão limpos** (`git ls-tree -r … | grep -c plano-merge-contatos-duplicados` = 0 nos cinco): a pasta de PII só existiu no `developer`, nunca na `main` — que é por isso que a F0 falava do `developer`.
  - **`--target` exige SHA COMPLETO.** SHA abreviado leva `HTTP 422: Release.target_commitish is invalid`. O plano não registrava isso.
  - `--latest=false` explícito nas 5 retroativas, `--latest` só na v0.7.0 — confirmado por `releases/latest`.
  - A nota da v0.7.0 foi atualizada antes de publicar para cobrir o commit da Luisa (cabeçalho desafogado + engrenagem na sidebar) e a contagem virou 73 commits.
- **Problemas / pendências:** **P2 (licença) continua aberta e agora está congelada em 6 releases.** O [README.md](../README.md) diz "Projeto de código aberto. Livre para uso pessoal e comercial." e **não existe arquivo `LICENSE`** — sem ele o padrão jurídico é *todos os direitos reservados*, o oposto do que o texto promete. Acrescentar o `LICENSE` depois vale para o repositório inteiro e **não exige refazer tag nenhuma**, então isso não bloqueou a publicação; mas continua sendo a pendência mais visível de um repositório público.
- **Verificação:** para cada uma das 6 tags, `git rev-parse <tag>^{commit}` + `git merge-base --is-ancestor <sha> main` — **as 6 apontam para commits da `main`**. Corpo de cada nota não-vazio (1.686 a 5.445 caracteres). `git ls-remote --tags origin` estava **vazio** antes de publicar (nenhuma tag órfã do plano 78 foi enviada junto).

---

## 6. Riscos e cuidados

| # | Ponto | Risco | Mitigação |
|---|---|---|---|
| R1 | PII já pública | `developer` e `origin/developer` têm o mesmo hash desde 05/08. `git clone` traz todos os branches por padrão | F0 estanca daqui pra frente. Apagar de verdade é **P1** (reescrita de histórico) |
| R2 | Janela de exposição no merge | Mergear antes da F0 põe PII na `main` publicada. Mesmo apagando depois, fica alcançável pelo **segundo pai do merge** para sempre | Ordem das waves é **obrigatória**: F0 antes de F8 |
| R3 | Suíte legada aborta | 5 call sites de **módulo** em `legacy_endpoints.py` (7.174 linhas). Não falha um teste — aborta o import e leva centenas de checks | F2 **antes** de F4, com critério de saída "verde com os plugins ainda presentes" |
| R4 | Paridade falsa | Versão maior ≠ superset. Já houve duas cópias do `protocolos` com a **mesma** versão e conteúdos distintos | F4·1: `diff -rq` obrigatório, comparar **conteúdo** |
| R5 | Doc mentindo | `CLAUDE.md` com ~40 links quebrados; a seção do `retornos` **já mente hoje** | F5, com verificação de link |
| R6 | README | Quem seguir literalmente não roda o produto — e a release é o que traz essa pessoa | F6 |
| R7 | Merge futuro | Merge + `git rm` ou merge seletivo deixam o `developer` com as pastas ⇒ **todo merge seguinte as reintroduz**, virando conflito `modify/delete` recorrente, com resolução errada republicando tudo em silêncio | D8: limpar o `developer` primeiro. Remove a **causa**, não o sintoma |
| R8 | Core grudado em commit misto | `_emit_created_domain` (do commit do trackify) e `isCollapsibleCard` (do commit do retornos) são genéricos e **precisam ficar** | A limpeza-primeiro os retém de graça. Poda por commit os perderia |
| R9 | "Latest" na release errada | Default da API REST é `make_latest=true` | F11·4: `--latest=false` explícito nas 5 retroativas |
| R10 | Notificar demais | 6 releases em sequência = 6 notificações — o oposto do objetivo | F11·1: rascunho primeiro, promover espaçado |
| R11 | Immutable Releases | Publicado, não dá para corrigir numeração/tag | F10·1: verificar antes |
| R12 | Sem CI | Não há verificação automática no repositório; a garantia depende de execução manual, **agora em dois repos** | F7 explícita. Considerar CI como trabalho futuro |
| R13 | Produto reduzido | Repo público passa a ter só o canal GOWA | É **intencional** (D1/D3 — instalação limpa + loja). F9·2 declara nas notas |
| R14 | `build_plugin_zips.py` sem fonte | `DEFAULT_SOURCE_DIR` aponta para o espelho podado | F4·3 |

---

## 7. Perguntas em aberto

**P1 — Reescrever o histórico público para apagar a PII?**
⏸️ **ADIADO** (2026-08-07, D4). O conteúdo já foi servido pelo GitHub desde 05/08. Reescrever (`git-filter-repo` + force-push nos 4 branches) tem **precedente neste repo** (plano 78, 23/07), mas quebra todo clone e fork existente e **não recolhe** o que já foi entregue.
(a) só remover da árvore e conviver · (b) **remover agora + agendar a reescrita separada** ← escolhido · (c) reescrever junto do merge.
**Recomendação:** manter (b). Misturar reescrita com merge junta duas operações de risco muito diferente.

**P2 — Qual licença?**
⏸️ **ADIADO** (2026-08-07, D6). O usuário informou "por enquanto é tudo liberado".
⚠️ **O efeito jurídico é o inverso da intenção:** sem arquivo `LICENSE`, o padrão é *todos os direitos reservados* — um cliente que receba a instalação não tem permissão nem para modificar, e o README hoje o convida a fazer exatamente isso.
(a) proprietária/uso restrito · (b) Apache-2.0 · (c) MIT · (d) decidir depois ← escolhido.
**Recomendação:** resolver **antes** da F11. Um `LICENSE` de uma linha já é melhor que nenhum.

**P3 — `docs-planos/` vai para a `main`?**
⏸️ **EM ABERTO.** São 25 planos internos. Explicam decisões que o `CLAUDE.md` só resume, mas citam instância e banco de produção (tratado na F1) e descrevem caminhos que deixarão de existir (o plano 105 tem 29 menções ao `retornos`; o 107 usa `assets/plugin_examples/retornos/static/retornos.js` como precedente).
(a) publicar como está · (b) publicar após a varredura da F1 + cabeçalho "registro histórico: caminhos podem não existir mais" · (c) não publicar (gitignore, como o `plano-merge`).
**Recomendação:** (b). Perder os planos empobrece o repositório público; link quebrado para plugin removido é histórico, e o cabeçalho resolve.

**P4 — Publicar os `.zip` dos 5 providers genéricos nas releases?**
✅ **DECIDIDO (2026-08-07): não** (D3). A distribuição é pela loja. Reavaliar se a loja demorar — sem ela e sem os zips, o repositório público não entrega canal além do GOWA.

**P5 — CI no repositório?**
⏸️ **EM ABERTO.** Não existe `.github/workflows/`. Com a suíte agora dividida em dois repositórios, a chance de regressão silenciosa aumenta. Fora do escopo deste plano.

---

## 8. Apêndice — arquivos-chave

**Remoção (árvore):**
- `plano-merge-contatos-duplicados/` (9 arquivos) · [.gitignore:47](../.gitignore#L47)
- `assets/plugin_examples/{telegram,whatsapp_cloud,facebook_messenger,instagram,website,protocolos,melhorias,retornos,trackify}/` (178 arquivos)

**Testes:**
- [tests/core/legacy/legacy_endpoints.py](../tests/core/legacy/legacy_endpoints.py) — `:683`, `:690`, `:2558`, `:5123`, `:5334` (módulo); `:6591`, `:6839` (função); `:5861,5867,5870,5873` (IP)
- [tests/integration/test_trackify_plugin.py](../tests/integration/test_trackify_plugin.py) — mover
- [tests/fake_provider.py](../tests/fake_provider.py) · [tests/plugin_test_utils.py:42](../tests/plugin_test_utils.py#L42) · [tests/plugin_fixtures.py](../tests/plugin_fixtures.py)
- [tests/support_js/smoke_plugin_screen.mjs](../tests/support_js/smoke_plugin_screen.mjs) · `lint_phantom_setters.mjs`

**Documentação:**
- [CLAUDE.md:819+](../CLAUDE.md#L819) · [README.md:30,193,239](../README.md#L30)
- [docs-planos/90-…:4](90-plano-escopo-do-websocket-por-canal.md) · [docs-planos/83-…:465](83-plano-extrair-plugins-do-core.md)

**Frontend (só comentário):**
- [web/static/js/services/messageView.js:117](../web/static/js/services/messageView.js#L117) · [web/static/js/components/contacts/ContactDetail.js:851](../web/static/js/components/contacts/ContactDetail.js#L851)

**Ferramentas:**
- [scripts/build_plugin_zips.py:44](../scripts/build_plugin_zips.py#L44) · [.claude/commands/release-up.md](../.claude/commands/release-up.md)

**Não tocar (referência):**
- [plugins/bootstrap.py:37](../plugins/bootstrap.py#L37) — `BUNDLED_AUTO_INSTALL = ("gowa",)`, já correto
- Todas as costuras de §3.2

---

## 9. Checklist de verificação

- [ ] `git ls-files | grep plano-merge-contatos-duplicados` vazio; diretório preservado em disco
- [ ] `grep -rnoE '\b10\.8\.[0-9]+\.[0-9]+\b'` (fora de `venv/`) sem resultado
- [ ] Nome comercial do cliente ausente de `docs-planos/`
- [ ] `diff -rq` de cada um dos 9 plugins contra o repo privado registrado no bloco da F4
- [ ] `ls assets/plugin_examples/` devolve só `gowa`
- [ ] `venv/bin/python -m pytest` **verde** no Postgres (`WHATSBOT_TEST_DB_URL`), sem outra suíte rodando em paralelo
- [ ] `cd ../whatsbot-pro-plugins && python3 scripts/test_plugins.py --all` verde
- [ ] `node --test` verde nos módulos puros
- [ ] Nenhum link de `CLAUDE.md` aponta para `assets/plugin_examples/<id>/` com `<id> != gowa`
- [ ] README executável por leitor externo: launcher com nome certo + `DATABASE_URL` documentada
- [ ] Boot limpo (instalação nova): `storages/plugins/` recebe só `gowa`, sem erro de plugin ausente
- [ ] Painel abre e a rota `/atendimentos` degrada para `/contacts` sem erro (plugin ausente)
- [ ] Modo escuro legível nas telas afetadas (nenhuma tela nova neste plano; conferir só o que a poda toca)
- [ ] `git diff main developer` vazio após a F8
- [ ] `gh release list` mostra 6 releases, **só a v0.7.0** marcada como Latest
- [ ] Nenhuma tag órfã promovida por engano (`git ls-remote --tags origin` só com v0.2.0–v0.7.0)
