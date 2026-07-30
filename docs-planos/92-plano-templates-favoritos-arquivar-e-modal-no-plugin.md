# Plano 92 — Templates: favoritos por atendente, arquivar o que não se usa, busca por conteúdo — com o modal migrando para o plugin

> **Status:** EM EXECUÇÃO (A0·A1·B1·C1·D1·E1·E2·E3·F1 ✅ · F2 🟡 publicado, falta o deploy · G1 ⬜ release seguinte) · **Data:** 2026-07-29 · **Escopo:** grande (frontend + plugin + 1 seam de core + 1 migration de plugin)
> **Origem:** pedido do usuário (2026-07-29) na tela "Enviar template" de um canal WhatsApp Cloud em produção. **Método:** leitura do código real com `arquivo:linha` verificado, `wc -l`/`grep -c` para toda medição, e consulta ao banco de desenvolvimento para a contagem de canais/usuários. Nenhum código foi alterado.
> Três funcionalidades pedidas (favoritos pessoais, marcar template morto, buscar pelo conteúdo) esbarram no mesmo fato: a tela é **do core** ([TemplatePicker.js](../web/static/js/components/contacts/TemplatePicker.js), 825 linhas) e o plugin `whatsapp_cloud` **não tem nenhum seam** para alcançá-la. O usuário decidiu resolver a causa: o modal inteiro passa a ser do plugin, e o vocabulário da Meta sai do core.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário (travadas — não reabrir)

| # | Decisão | Data | Consequência no plano |
|---|---------|------|------------------------|
| D1 | ✅ **O modal inteiro migra para o plugin `whatsapp_cloud`** (não seams pontuais) | 2026-07-29 | O core ganha **um** ponto de override exclusivo; as 825 linhas do `TemplatePicker.js` viram arquivo do plugin. As 3 funcionalidades novas nascem lá dentro |
| D2 | ✅ **Arquivar = some da lista, GLOBAL** (um marca, some para todos), reversível por filtro | 2026-07-29 | Nada é apagado na Meta — a lixeira que já existe (`template.delete`) continua sendo a exclusão de verdade. Tabela por `(channel_id, template_name)` |
| D3 | ✅ **Permissão nova e separada, SEM concessão automática** | 2026-07-29 | `plugin.whatsapp_cloud.template_archive` declarada no `rbac:` do manifest; o usuário a distribui nos cargos pela tela de Usuários. **Zero** código de seeding, zero escrita do plugin em tabela do core |
| D4 | ✅ **Favoritos no servidor, por usuário** | 2026-07-29 | Tabela `(user_id, channel_id, template_name)`. Sem usuário logado (instalação aberta) a estrela não aparece — não há a quem pertencer |
| D5 | ✅ **A extração do que é WhatsApp Cloud no core é FASE FINAL deste plano**, não plano separado | 2026-07-29 | Wave 4. O form de criação sai de graça junto com D1; o que sobra é o backend (§7) |
| D6 | ✅ **Tirar o máximo possível do WhatsApp Cloud do core** | 2026-07-29 | Onde "máximo possível" colide com uma decisão já escrita no próprio código (`message_errors.py`, `LEGACY_CLOUD_VIDEO_LIMITS`), o plano **declara o conflito** em vez de decidir sozinho — ver P3 |

Princípio herdado do repo, aplicável aqui: **verde a cada fase**; **um refactor por commit**; migração fiel primeiro, funcionalidade nova depois (nunca no mesmo commit).

---

## 1. Resumo executivo

O atendente abre "Enviar template" e recebe **a lista inteira do WABA**, sem ordem útil, com dezenas de templates mortos misturados aos 5 que ele usa de fato, e uma busca que só casa `name` e `category` — não o texto do template. Isso é resolvido com favoritos (pessoais), arquivar (global, sob permissão) e busca por conteúdo.

O obstáculo não é a funcionalidade — é o **dono da tela**. O modal, as rotas, as validações e as permissões são do core; o plugin só fala com a Graph API. Adicionar as três funcionalidades no core aumentaria o vocabulário da Meta lá dentro, exatamente na direção contrária à que o projeto vem seguindo (plano 76 tirou o `WebhookHealthRow`, plano 83 planeja tirar as pastas dos plugins).

O caminho é: **(1)** abrir no core um ponto de override exclusivo e genérico (nenhum nome de provider), **(2)** migrar o modal para o plugin **sem mudar comportamento**, **(3)** construir as três funcionalidades já dentro do plugin, **(4)** tirar do core as regras da Meta que sobraram no backend, trocando constantes por uma **capability declarada pelo provider** — o mesmo padrão de `MediaLimits` (plano 65).

---

## 2. Como funciona hoje (mapa verificado)

### 2.1 A tela é do core, ponta a ponta

| Camada | Onde | Tamanho |
|---|---|---|
| Modal (lista, busca, envio, criação, exclusão) | [TemplatePicker.js](../web/static/js/components/contacts/TemplatePicker.js) | **825 linhas** |
| ↳ `CreateTemplateForm` (form de criação Meta-shaped) | [TemplatePicker.js:545-813](../web/static/js/components/contacts/TemplatePicker.js#L545) | 269 linhas |
| Cliente HTTP (10 funções) | [api.js:654-719](../web/static/js/services/api.js#L654) | — |
| Rotas conv-scoped (5) | [conversations.py:741](../server/routes/conversations.py#L741), `:772`, `:837`, `:897`, `:926` | — |
| Rotas channel-scoped (6, com `session-state`) | [channels.py:72](../server/routes/channels.py#L72), `:92`, `:117`, `:157`, `:205`, `:235` | — |
| Serviço + validações | [template_service.py](../app/services/template_service.py) | 329 linhas |
| Permissões `template.create` / `template.delete` | [permission_catalog.py:51-52](../domain/permission_catalog.py#L51) | — |
| Chamadas Graph (listar/criar/apagar/upload) | [whatsapp_cloud/channels.py:633-900](../assets/plugin_examples/whatsapp_cloud/channels.py#L633) | plugin ✅ |

O modal é montado em **dois** lugares:

| Ponto | Modo | Linha |
|---|---|---|
| Conversa aberta (compositor) | `conversationId` | [ContactDetail.js:633-641](../web/static/js/components/contacts/ContactDetail.js#L633) |
| "Novo atendimento" (ainda sem conversa) | `channelId` + `phone` | [NewConversationModal.js:562-568](../web/static/js/components/contacts/NewConversationModal.js#L562) |

E o botão que o abre está em [Composer.js:274-284](../web/static/js/components/contacts/Composer.js#L274), gated **só** por `templatesSupported` (capability do canal, resolvida em [conversations.py:426](../server/routes/conversations.py#L426)).

### 2.2 O que o payload já entrega (e a busca ignora)

`list_templates` pede à Graph `fields=name,language,status,category,components` ([channels.py:656](../assets/plugin_examples/whatsapp_cloud/channels.py#L656)) e normaliza cada componente preservando `text`, `format`, `example` e `buttons` ([channels.py:893-905](../assets/plugin_examples/whatsapp_cloud/channels.py#L893)). Ou seja: **o texto do template já chega ao navegador**. O filtro atual simplesmente não olha para ele:

```js
return (t.name || '').toLowerCase().includes(q) || (t.category || '').toLowerCase().includes(q);
//                                        TemplatePicker.js:333-337 — nome e categoria, só
```

⚠️ **Gotcha:** a lista é cacheada 5 min no provider ([channels.py:648-651](../assets/plugin_examples/whatsapp_cloud/channels.py#L648)), invalidada por create/delete. Favoritos e arquivados **não** podem entrar nesse cache — são estado local, com granularidade por usuário. Devem ser buscados numa chamada separada e fundidos no cliente.

### 2.3 A camada de extensão de frontend já existe — e é boa

| Primitiva | Onde | Semântica |
|---|---|---|
| `api.addSlot(name, C)` | [registry.js:130-138](../web/static/js/plugins/registry.js#L130) | **aditivo** (N componentes) |
| `api.addFilter(name, fn, prio)` | [registry.js:97-105](../web/static/js/plugins/registry.js#L97) | cadeia por prioridade; `null` aborta |
| `api.overrideRoute(tabId, C)` | [registry.js:148-161](../web/static/js/plugins/registry.js#L148) | **EXCLUSIVO** — 1º registra, os outros são logados e ignorados |
| `api.ui.openModal(fn)` | [ModalHost.js:28-46](../web/static/js/plugins/ModalHost.js#L28) | modal do plugin com `await` da resposta |
| `api.http` | [api.js:121-149](../web/static/js/plugins/api.js#L121) | transporte namespaceado em `/api/plugins/<id>`, status-aware |
| `api.services` | [api.js:88-108](../web/static/js/plugins/api.js#L88) | allowlist congelada do `services/api.js` do core |
| `plugin_permission(k)` / `core_permission(k)` | [context.py:286](../plugins/context.py#L286), [:321](../plugins/context.py#L321) | 403 com envelope unificado; default-allow em instalação aberta |
| `rbac:` no manifest | [rbac.py:22-50](../plugins/rbac.py#L22) | vira `plugin.<id>.<key>` no `PermissionPicker` |

✅ **Verificado:** as 10 funções de template do core (`getConversationTemplates`, `sendChannelTemplate`, …) **não estão** na `PLUGIN_SERVICES_DENY` ([api.js:52-75](../web/static/js/plugins/api.js#L52)) — o plugin as alcança por `api.services` sem nenhuma mudança de contrato.

❌ **O que falta:** o `TemplatePicker` não tem slot, filtro nem override. É o único ponto onde o core precisa mudar para D1.

### 2.4 O plugin `whatsapp_cloud` hoje

| Item | Estado |
|---|---|
| Versão | `1.7.0` ([plugin.yaml:3](../assets/plugin_examples/whatsapp_cloud/plugin.yaml#L3)) — **instalada == bundled** (`diff -rq` limpo) |
| `frontend_extends` | ✅ existe, registra 1 slot ([extends.js:16](../assets/plugin_examples/whatsapp_cloud/static/extends.js#L16)) |
| `routes.py` | 4 rotas, todas `core_permission("channel.manage")` ([routes.py:216-312](../assets/plugin_examples/whatsapp_cloud/routes.py#L216)) |
| `migrations/` | **não existe** — este plano cria a primeira |
| `tests/` | **não existe** — este plano cria |
| `rbac:` no manifest | **não existe** — este plano cria |

⚠️ **Gotcha documentado no próprio plugin** ([routes.py:12-15](../assets/plugin_examples/whatsapp_cloud/routes.py#L12)): *"não importar a classe `WhatsAppCloudChannel` — submódulos não estão no `sys.path` sob o plugin loader"*. As rotas novas deste plano só tocam o banco, então não esbarram nisso. **A confirmar** se a observação ainda vale (o `facebook_messenger` faz `from .meta_graph import …` e funciona) — não é bloqueador aqui.

---

## 3. Inventário do trabalho

| # | Item | Onde | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|
| I1 | Override exclusivo de componente | [registry.js](../web/static/js/plugins/registry.js) + [api.js:160](../web/static/js/plugins/api.js#L160) | `overrideComponent(name, C)` espelhando `overrideRoute` (1º vence, conflito logado) | baixo | S |
| I2 | Host do picker + gate do botão | [ContactDetail.js:633](../web/static/js/components/contacts/ContactDetail.js#L633), [NewConversationModal.js:562](../web/static/js/components/contacts/NewConversationModal.js#L562), [Composer.js:274](../web/static/js/components/contacts/Composer.js#L274) | `<TemplatePickerHost>`: override → fallback core. Botão exige picker resolvível | médio | M |
| I3 | Migração fiel do modal | core → `whatsapp_cloud/static/TemplatePicker.js` | cópia byte-fiel; imports do core viram `api.services`/`api.http` | **alto** | L |
| I4 | Migration + repo do plugin | `whatsapp_cloud/migrations/001_*.sql` | 2 tabelas `plugin_whatsapp_cloud_*` | baixo | S |
| I5 | Rotas de preferências | `whatsapp_cloud/routes.py` | `GET/POST` prefs; archive sob `plugin_permission` | baixo | M |
| I6 | `rbac:` no manifest | `whatsapp_cloud/plugin.yaml` | 1 chave, grupo "Templates (WhatsApp Cloud)" | baixo | S |
| I7 | UI de favoritos | picker no plugin | estrela por linha + ordenação | baixo | M |
| I8 | UI de arquivar + chips | picker no plugin | ícone + filtro Todas/Favoritas/Arquivadas | baixo | M |
| I9 | Busca por conteúdo | picker no plugin | predicado sobre `components[]` | baixo | S |
| I10 | `TemplateSpec` (capability) | [base.py:151](../channels/base.py#L151) + [template_service.py:25-160](../app/services/template_service.py#L25) | provider declara, core avalia — padrão `MediaLimits` | médio | L |
| I11 | Remoção do fallback core | `TemplatePicker.js` do core | só na release seguinte (§8, R2) | médio | S |
| I12 | Zip + publicação | `assets/channel_plugins/` + repo de plugins | reconciliar conteúdo antes | **alto** | M |

### 3.1 Falsos positivos descartados

| Candidato | Por que NÃO é trabalho deste plano |
|---|---|
| As 5 rotas conv-scoped + 6 channel-scoped de template | São **genéricas por capability** (`outbound.supports(channel_id, "templates")`), sem um `if provider ==`. Movê-las para `/api/plugins/whatsapp_cloud/` obrigaria o plugin a fazer `save_operator_message` + broadcast + emit do bus, quebraria **92 checks** de `tests/test_endpoints.py` e a compatibilidade de API. Ficam no core |
| `server/message_errors.py` (7 menções à Meta) | O próprio arquivo **documenta a decisão** de ficar no core ([message_errors.py:8-15](../server/message_errors.py#L8)): *"dicionário inerte — vocabulário do protocolo, não comportamento de provider… quando um 2º provider trouxer um espaço de códigos próprio, isto vira um gancho `describe_status_error()`"*. Reabrir isso exige o 2º provider, que não existe. Ver P3 |
| `LEGACY_CLOUD_VIDEO_LIMITS` ([video_validate.py:36](../channels/video_validate.py#L36)) + [video_transcode.py:30](../channels/video_transcode.py#L30) | O plano 83 §F5 declara: *"Fica no core, e **está certo que fique** — são valores duplicados, não import do plugin"* (fallback retrocompat para plugin anterior ao plano 65) |
| As ~50 menções a "WhatsApp Cloud"/"Meta" em [base.py](../channels/base.py), [channel_webhook.py](../server/routes/channel_webhook.py), [contacts.py](../server/routes/contacts.py), [outbound.py](../channels/outbound.py) | Medidas uma a uma: são **comentários e docstrings** citando o Cloud como exemplo do porquê de uma regra genérica. Zero comportamento. Reescrevê-las é ruído de diff sem ganho |
| `server/execution.py` (3 hits de "meta") | Falso positivo de grep: é a variável local `meta` de `_active.pop()` ([execution.py:64](../server/execution.py#L64)) |
| Busca por conteúdo como mudança de core | Com D1 o modal é do plugin — a busca vai junto. Não sobra nada no core |
| `session-state` / janela de 24h ([channels.py:72](../server/routes/channels.py#L72)) | Genérico por `session_window_hours`; o Telegram declara `0h` e o GOWA nada. Não é Meta |

---

## 4. Arquitetura

### 4.1 O seam no core (I1 + I2)

Um registro **exclusivo** de componente, irmão do `overrideRoute` e com a mesma política anti-conflito:

```js
// registry.js — novo, genérico: nenhum nome de provider, nenhum nome de plugin
export function overrideComponent(name, component, pluginId = 'core')  // 1º vence; conflito → console.warn
export function getComponentOverride(name)                              // {pluginId, component} | null
```

Exposto como `api.overrideComponent(name, C)` em [api.js:160-197](../web/static/js/plugins/api.js#L160). É adição pura ⇒ `FRONTEND_API_VERSION` continua `'1.0'` (bump MINOR só quando houver casa decimal; o contrato atual é major-only — [api.js:151-158](../web/static/js/plugins/api.js#L151)).

O core passa a montar um **host** em vez do componente direto:

```
<TemplatePickerHost conversationId channelId phone onClose onSent />
   └─ getComponentOverride('template.picker')  →  componente do plugin
   └─ senão                                    →  TemplatePicker do core (fallback, some em I11)
```

E o botão do compositor ([Composer.js:274](../web/static/js/components/contacts/Composer.js#L274)) passa a exigir `templatesSupported && pickerAvailable`, onde `pickerAvailable = !!override || FALLBACK_EXISTS`. Sem isso, o dia em que o fallback sair (I11) o botão abriria o vazio.

⚠️ **Corrida de boot:** os `extends.js` carregam **depois do primeiro paint** ([App.js:54-74](../web/static/js/components/shell/App.js#L54)) e o `ScreenRouter` já trata essa janela com o gate `extensionsLoaded` ([ScreenRouter.js:163-166](../web/static/js/components/shell/ScreenRouter.js#L163)). Para o picker o risco é **menor** (o modal só monta quando o operador clica, muito depois do boot), mas o `<Slot>`/host deve se re-renderizar pelo `subscribe()` do registry ([Slot.js:12](../web/static/js/plugins/Slot.js#L12)) — o mesmo mecanismo, não um novo.

### 4.2 O backend do plugin (I4 + I5 + I6)

```sql
-- migrations/001_template_prefs.sql   (prefixo plugin_whatsapp_cloud_ obrigatório)
plugin_whatsapp_cloud_template_favorites (id, user_id, channel_id, template_name, created_at)
  UNIQUE (user_id, channel_id, template_name)
plugin_whatsapp_cloud_template_archived  (id, channel_id, template_name, archived_by, archived_at)
  UNIQUE (channel_id, template_name)
```

⚠️ **Gotcha do migrator:** ele **splita o arquivo em `;` antes de tirar comentários** ([migrator.py:111-116](../plugins/migrator.py#L111)) — nenhum comentário SQL pode conter `;`. E toda `CREATE TABLE`/`CREATE INDEX` é validada contra o prefixo ([migrator.py:141-150](../plugins/migrator.py#L141)).

| Rota (`/api/plugins/whatsapp_cloud/…`) | Gate | Devolve |
|---|---|---|
| `GET /template-prefs?channel_id=` | nenhum (leitura) | `{favorites:[name], archived:[name], can_archive:bool}` |
| `POST /template-prefs/favorite` | nenhum — **pessoal** | alterna `{channel_id, name, favorite}` para o usuário da request |
| `POST /template-prefs/archive` | `plugin_permission("template_archive")` | alterna `{channel_id, name, archived}` |

O `user_id` sai de `request.state.user["id"]`, setado pelo middleware de auth ([app.py:579-589](../server/app.py#L579)). Sem usuário (instalação aberta), `GET` devolve `favorites: []` e o `POST` de favorito responde 400 — a estrela não é renderizada (D4).

**Auditoria** (regra de [docs/PLUGINS_AUDITAVEIS.md](../docs/PLUGINS_AUDITAVEIS.md)): arquivar/desarquivar é *mudança de estado com dono* ⇒ `audit("whatsapp_cloud", "template.archive", resource_type="channel", resource_id=channel_id, before=…, after=…)` — plugin de canal grava **no canal**, para o filtro por canal devolver a história inteira. Favoritar **não** é auditado (preferência pessoal por usuário está na lista do "o que não auditar").

Manifest:

```yaml
rbac:
  group: "Templates (WhatsApp Cloud)"     # cai colado nas duas chaves core que já aparecem lá
  permissions:
    - { key: template_archive, label: "Arquivar templates não usados" }
```

Sem concessão automática (D3): a chave nasce sem dono e o usuário a distribui em Usuários → Cargos.

### 4.3 As três funcionalidades (I7 + I8 + I9), todas dentro do picker do plugin

| Funcionalidade | Comportamento |
|---|---|
| Favoritos | Estrela por linha (cheia/vazia). Favoritos sobem para o topo, mantendo entre si a ordem que a Meta devolveu |
| Arquivar | Ícone ao lado da lixeira, só com a permissão. Arquivado **some** da lista padrão |
| Filtro | Chips `Todas · Favoritas · Arquivadas`; "Arquivadas" mostra só as arquivadas (é como se desarquiva) |
| Busca | Casa `name`, `category` **e** o texto de `components[]`: `header.text`, `body.text`, `footer.text`, `buttons[].text` e `buttons[].url` |

**Ordem de aplicação** (importa): `arquivados fora` → `chip` → `busca` → `favoritos primeiro`. Um template arquivado **não** reaparece por casar a busca, exceto no chip "Arquivadas".

### 4.4 A extração final (I10)

Hoje o core carrega as regras da Meta como constantes de módulo:

| Constante | Linha | O que é |
|---|---|---|
| `TEMPLATE_CATEGORIES` | [template_service.py:25](../app/services/template_service.py#L25) | `UTILITY/MARKETING/AUTHENTICATION` |
| `TEMPLATE_HEADER_FORMATS` | [:32](../app/services/template_service.py#L32) | `IMAGE/VIDEO/DOCUMENT` |
| `TEMPLATE_BUTTON_TYPES` + `BUTTON_TYPE_MAX` | [:33-38](../app/services/template_service.py#L33) | 4 tipos + regra de mistura (2 URL, 1 phone, 1 copy) |
| `BUTTON_TEXT_MAX` / `BUTTONS_MAX` | [:34-35](../app/services/template_service.py#L34) | 25 / 10 |
| `UPLOAD_EXAMPLE_MIMES` + `UPLOAD_EXAMPLE_MAX_BYTES` | [:41-49](../app/services/template_service.py#L41) | whitelist + 16 MiB |

A saída é o padrão que o repo já usa para mídia: **o provider declara, o core avalia**, zero `if provider ==`.

```python
# channels/base.py — irmão de MediaLimits (base.py:151)
@dataclass
class TemplateSpec:
    categories: frozenset[str]
    header_formats: frozenset[str]
    button_types: frozenset[str]
    button_type_max: dict[str, int]
    button_text_max: int
    buttons_max: int
    upload_mimes: frozenset[str]
    upload_max_bytes: int
    name_pattern: str          # o `^[a-z0-9_]+$` que hoje é regex solta na rota

ChannelCapabilities.template_spec: TemplateSpec | None = None   # None ⇒ core não restringe
```

`normalize_buttons` / `normalize_header_media` / `validate_example_upload` passam a receber o spec. O `whatsapp_cloud` declara o dele; o core fica sem vocabulário da Meta.

⚠️ **Consequência real, decidida em P2:** com `template_spec=None` o core deixa de devolver 400 e o erro passa a vir da Meta (502). Isso muda 3 checks existentes ("nome inválido → 400", "sem body_text → 400", "categoria inválida → 400", [test_endpoints.py:6306-6313](../tests/test_endpoints.py#L6306)) — o `_FakeTplChannel` ([test_endpoints.py:6180-6250](../tests/test_endpoints.py#L6180)) passa a declarar um spec e os checks continuam válidos.

---

## 5. Fases e paralelização

```
WAVE 0   A0(baseline) · A1(paridade do zip)                     🟢 em paralelo
            │ (barreira: A1 é pré-requisito de ENTREGA, não de código)
WAVE 1   B1(seam no core)                                       🔴 sozinha  [bloqueia: C1]
            │
WAVE 2   C1(migração fiel do modal) · D1(backend do plugin)     🟢 em paralelo (arquivos disjuntos)
            │ (barreira: C1 e D1 juntos habilitam a Wave 3)
WAVE 3   E1(favoritos) → E2(arquivar+chips)  ·  E3(busca)       E1→E2 sequencial (mesmo arquivo); E3 🟢
            │
WAVE 4   F1(TemplateSpec) · F2(zip+docs)                        🟢 em paralelo
            │
WAVE 5   G1(remover fallback do core)                           🔴 sozinha — RELEASE SEGUINTE
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | A0 | testes | 🟢 | baixo | baseline verde registrado |
| 0 | A1 | distribuição | 🟢 | alto | conteúdo reconciliado entre core, instalado e repo de plugins |
| 1 | B1 | core (frontend) | 🔴 | médio | sem plugin registrando, a tela é byte-idêntica |
| 2 | C1 | plugin (frontend) | 🟢 | alto | modal abre pelo plugin; 5 fluxos íntegros |
| 2 | D1 | plugin (backend) | 🟢 | baixo | migration aplica; 3 rotas respondem; permissão no picker |
| 3 | E1 | plugin | 🔴 `[depois de C1+D1]` | baixo | estrela persiste por usuário |
| 3 | E2 | plugin | 🔴 `[depois de E1]` | baixo | arquivado some para todos; chip devolve |
| 3 | E3 | plugin | 🟢 | baixo | busca acha pelo corpo |
| 4 | F1 | core (backend) | 🟢 | médio | `template_service` sem constante da Meta; suíte verde |
| 4 | F2 | distribuição | 🟢 | médio | zip publicado com bump real |
| 5 | G1 | core | 🔴 | médio | `TemplatePicker.js` sai do core **com o zip já em produção** |

---

### Fase A0 — Baseline e caracterização

**Objetivo:** saber exatamente o que está verde antes de mexer, e o que cobre a tela.

**Itens**
1. `[paralelo]` Rodar `venv/bin/python tests/test_endpoints.py` e registrar o total de checks e falhas. ⚠️ `pytest tests/` **não roda inteiro** (vários arquivos são scripts com `sys.exit`) — rodar por arquivo.
2. `[paralelo]` Registrar os **92 checks** da seção de templates ([test_endpoints.py:6180-6667](../tests/test_endpoints.py#L6180)) como o contrato do backend: eles usam `_FakeTplChannel`, **não** o plugin real ⇒ continuam válidos depois da migração do modal.
3. `[paralelo]` Confirmar que não existe teste JS do `TemplatePicker` (`ls web/static/js/components/contacts/*.test.js`) — a migração de I3 **não tem rede de segurança automatizada**; o roteiro manual do item 4 é a rede.
4. `[sequencial]` Escrever o roteiro manual dos 5 fluxos que a Wave 2 tem de preservar: **listar · enviar (com variáveis) · criar · upload de exemplo de mídia · apagar**, nos **dois** modos (conversa aberta e "Novo atendimento").

**Pronto quando:** o baseline está escrito no bloco de status abaixo, com número de checks e o roteiro dos 5 fluxos × 2 modos.

#### Status de execução — Fase A0
**Estado:** ✅ Concluída (2026-07-29 18:10) — **nenhum arquivo de produto tocado**
- **O que foi feito:** baseline medido; seção de contrato delimitada; ausência de cobertura JS confirmada; roteiro manual escrito (§9.1).
- **Como foi feito / decisões:**
  1. `venv/bin/python tests/test_endpoints.py` → **1626 passed, 0 failed**. Rodado como script (não por `pytest tests/`, que não coleta — vários arquivos são scripts com `sys.exit`).
  2. Seção de templates delimitada e **medida**: **92 checks** em `:6180-6667` (o número no rascunho do plano, 60, estava subestimado — a seção segue além de `:6460`, onde começa o bloco channel-scoped do plano 21), mais **11 checks** de mídia/janela 24h em `:6668-6772`. Todos exercitam o `_FakeTplChannel` (`:6180-6250`), **não** o plugin real ⇒ permanecem válidos depois da migração do modal (confirma o falso positivo da §3.1).
  3. Cobertura JS: o único `*.test.js` em `components/contacts/` é `menuLayout.test.js`. **Confirmado: não há teste do `TemplatePicker`** — a C1 não tem rede automatizada, o roteiro da §9.1 é obrigatório.
- **Problemas / pendências:** a execução do plano 88 registrou falhas **não determinísticas** ao rodar o *diretório* `tests/characterization` (interferência entre arquivos, agravada pelo WIP não-commitado de terceiros na árvore). Não afeta esta fase — `test_endpoints.py` roda isolado e deu verde limpo. Se a suíte de caracterização for usada como gate em alguma fase, rodar **por arquivo**.
- **Verificação:** 1626/1626 verde, registrado como o número a reproduzir ao fim de cada fase deste plano.

---

### Fase A1 — Paridade do zip (pré-requisito de ENTREGA)

**Objetivo:** garantir que publicar o plugin não **regride** o que está rodando.

**Itens**
1. `[sequencial]` Comparar **CONTEÚDO** (não número de versão) entre: `assets/plugin_examples/whatsapp_cloud/` (`1.7.0`), `storages/plugins/whatsapp_cloud/` (`1.7.0`, `diff -rq` já limpo em 2026-07-29), o `whatsapp_cloud-plugin.zip` de [assets/channel_plugins/](../assets/channel_plugins/) e o publicado em `Techify-one/whatsbot-pro-plugins`.
2. `[sequencial]` Consultar a tabela `plugins` da **instância de produção** antes de afirmar paridade — já houve caso de produção rodando versão que nunca existiu no git.

⚠️ O plano 83 §2 mediu `whatsapp_cloud` **1.4.0** publicado contra **1.5.0** no core em 25/07, e documenta que *"publicar em lote destrói trabalho"*. Hoje o core está em `1.7.0` — a defasagem provavelmente cresceu.

**Pronto quando:** existe uma linha por origem dizendo qual conteúdo é o mais novo, e a decisão de qual é a base para o bump deste plano.

#### Status de execução — Fase A1
**Estado:** ✅ Concluída (2026-07-29 18:10) — **veredito: linhagem única, A1 NÃO bloqueia**
- **O que foi feito:** as 5 origens comparadas por **conteúdo**, não por número de versão.

| Origem | Versão | Conteúdo |
|---|---|---|
| `assets/plugin_examples/whatsapp_cloud/` (git HEAD) | **1.7.0** | **BASE** |
| `storages/plugins/whatsapp_cloud/` (dev instalado) | 1.7.0 | idêntico (`diff -rq` limpo) |
| `assets/channel_plugins/whatsapp_cloud-plugin.zip` (27/07 19:55) | 1.7.0 | idêntico |
| repo de plugins publicado (`f3e3bf0`) | **1.4.0** | **estritamente mais velho**: `channels.py` 1117×1214, `routes.py` 301×335, e **não tem** `static/extends.js` nem `static/WebhookHealthRow.js` |
| **produção** (`whatsbot@10.8.100.5`, tabela `plugins`) | **1.6.0** | **ancestral limpo**: `git diff afdb503 HEAD` = só `routes.py` +33 linhas (seam de auditoria, `42a9aac`) + o bump |

- **Como foi feito / decisões:** clone raso do `Techify-one/whatsbot-pro-plugins` no scratchpad + `unzip` + `diff -rq`; produção consultada por `SELECT id, version, enabled FROM plugins` (read-only, via MCP do cofre).
- **Problemas / pendências:** 🚨 **o publicado (1.4.0) não é candidato a base** — publicar a partir dele apagaria o `WebhookHealthRow` e o `extends.js`, ou seja, desfaria o frontend do **plano 76** e ~131 linhas de backend. Registrar isso na F2. Produção (1.6.0) perde só o seam de auditoria; o **Atualizar** da F2.4 a leva para a versão deste plano.
- **Verificação:** ao contrário de `telegram` e `protocolos` (que divergiram nos dois sentidos), aqui a linhagem é **unidirecional**: `1.4.0 (publicado) ⊂ 1.6.0 (produção) ⊂ 1.7.0 (local)`. Nenhum trabalho a resgatar.

---

### Fase B1 — O seam no core 🔴

**Objetivo:** abrir **um** ponto de override genérico, sem que nada mude enquanto ninguém o usa.

**Itens**
1. `[sequencial]` `overrideComponent` / `getComponentOverride` em [registry.js](../web/static/js/plugins/registry.js), espelhando `overrideRoute` ([:148-162](../web/static/js/plugins/registry.js#L148)): 1º registrante vence, conflito vai para `console.warn`, `bump()` no fim. Incluir no `reset()` ([:178](../web/static/js/plugins/registry.js#L178)) e no `inventory()` ([:187-198](../web/static/js/plugins/registry.js#L187) — exportado como `registryInventory` em [App.js:14](../web/static/js/components/shell/App.js#L14)).
2. `[sequencial]` Expor `overrideComponent` em `buildPluginApi` ([api.js:167-172](../web/static/js/plugins/api.js#L167)).
3. `[sequencial]` Criar `TemplatePickerHost` (core) que resolve override → fallback, re-renderizando via `subscribe()`.
4. `[paralelo]` Trocar as duas montagens: [ContactDetail.js:634](../web/static/js/components/contacts/ContactDetail.js#L634) e [NewConversationModal.js:563](../web/static/js/components/contacts/NewConversationModal.js#L563).
5. `[paralelo]` Gate do botão em [Composer.js:274](../web/static/js/components/contacts/Composer.js#L274) → `templatesSupported && pickerAvailable`.
6. `[sequencial]` Documentar o novo contrato no cabeçalho de [registry.js:22-77](../web/static/js/plugins/registry.js#L22) (a seção "STABLE FRONTEND EXTENSION CONTRACTS").

**Pronto quando:** com o plugin **sem** registrar nada, abrir uma conversa Cloud e percorrer os 5 fluxos do roteiro A0.4 — comportamento idêntico. `window.__whatsbotExtensions` mostra a nova categoria vazia.

#### Status de execução — Fase B1
**Estado:** ✅ Concluída no código (2026-07-29 18:30) · ⏸️ **validação em navegador pendente** (roteiro §9.1)
- **O que foi feito:**

| Arquivo | Mudança |
|---|---|
| `web/static/js/plugins/registry.js` | mapa `_components`; `overrideComponent`/`getComponentOverride` (exclusivos, espelhando `overrideRoute`); entram no `reset()` e no `inventory()`; contrato documentado no cabeçalho |
| `web/static/js/plugins/api.js` | `api.overrideComponent(name, C)` no `buildPluginApi` |
| `web/static/js/components/contacts/TemplatePickerHost.js` | **novo** (52 linhas): resolve override → fallback; exporta `TEMPLATE_PICKER_SLOT` e `templatePickerAvailable()` |
| `ContactDetail.js` · `NewConversationModal.js` | as duas montagens passam a usar o host |
| `Composer.js` | botão gated por `templatesSupported && templatePickerAvailable()`; a faixa de 24h degrada o link para texto |

- **Como foi feito / decisões:**
  1. **Override exclusivo, não slot aditivo** — um modal não pode ser renderizado N vezes; a semântica correta é a do `overrideRoute` (1º registra vence, conflito logado).
  2. `templatePickerAvailable()` hoje devolve **sempre `true`** (o fallback do core existe) ⇒ a B1 é um no-op observável, como o plano exige. Na G1 a mudança fica em **uma constante** (`CORE_FALLBACK`).
  3. A faixa "Fora da janela de 24h" **degrada o link para texto simples** em vez de sumir: a informação continua verdadeira mesmo sem tela para abrir.
  4. `FRONTEND_API_VERSION` **não** mudou — a adição é pura e o guard é major-only ([api.js:151-158](../web/static/js/plugins/api.js#L151)).
- **Problemas / pendências:**
  1. 🔎 **Achado para a G1 — 4 call sites além do botão** chamam `openTemplatePicker()`: [useComposer.js:219](../web/static/js/components/contacts/hooks/useComposer.js#L219) e [:334](../web/static/js/components/contacts/hooks/useComposer.js#L334), [useMediaUpload.js:436](../web/static/js/components/contacts/hooks/useMediaUpload.js#L436) e [:457](../web/static/js/components/contacts/hooks/useMediaUpload.js#L457) — são os desvios de "texto/mídia fora da janela de 24h", que **abortam o envio** e abrem o modal. Hoje inócuos (há fallback). **Depois da G1, com o plugin desativado**, eles abortariam o envio e abririam um host que renderiza `null` ⇒ o operador fica sem feedback. A G1 tem de tratar: ou o host renderiza um aviso quando não há picker, ou esses 4 sites consultam `templatePickerAvailable()` antes de desviar. **Adicionado ao escopo da G1.**
  2. Roteiro §9.1 não executado (precisa de navegador logado).
- **Verificação:** `node --test` **363/363**; `tests/test_endpoints.py` **1635/1635**; `node --input-type=module --check` verde nos 6 módulos; `grep` confirma que nenhuma referência ao `TemplatePicker` sobrou fora do host. ⚠️ O baseline subiu de 1626 (A0) para 1635 porque outro agente commitou os planos 87/89 e o teste do `vendas_ia` durante a execução — **a B1 não tocou em nenhum `.py`**, então a variação não é dela.

---

### Fase C1 — Migração fiel do modal para o plugin 🟢 `[depende de: B1]`

**Objetivo:** mesmo modal, outro dono. **Zero** funcionalidade nova nesta fase — é a regra "um refactor por commit".

**Itens**
1. `[sequencial]` Copiar `TemplatePicker.js` para `assets/plugin_examples/whatsapp_cloud/static/TemplatePicker.js`, **sem reescrever** — só trocar a fronteira de imports:
   - as 10 funções de [api.js:654-719](../web/static/js/services/api.js#L654) passam a vir de `api.services` (verificado: nenhuma está na deny-list);
   - `formatPhoneDisplay` ([TemplatePicker.js:16](../web/static/js/components/contacts/TemplatePicker.js#L16)) **não** está alcançável por `api.services`: ela mora em [utils/phone.js:37](../web/static/js/utils/phone.js#L37) e a allowlist é construída só a partir de `services/api.js` ([api.js:88-92](../web/static/js/plugins/api.js#L88)). Duas saídas: inlinear no plugin (é formatação pura, ~15 linhas) ou importar por URL absoluta (`/static/js/utils/phone.js`), padrão que o CLAUDE.md já autoriza para tela de plugin. **Preferir inlinear** — o zip fica autossuficiente e não quebra se o core mover o arquivo.
2. `[sequencial]` `extends.js` passa a registrar também `api.overrideComponent('template.picker', TemplatePicker)` ([extends.js:16](../assets/plugin_examples/whatsapp_cloud/static/extends.js#L16)).
3. `[sequencial]` Bump de versão no `plugin.yaml` (a base sai de A1).
4. `[sequencial]` Executar o roteiro A0.4 inteiro: 5 fluxos × 2 modos.
5. `[sequencial]` Desativar o plugin e repetir 2 fluxos — o fallback do core assume, o botão continua funcionando.

⚠️ **Risco alto por natureza:** é uma tela de operação crítica sem cobertura automatizada (A0.3). O roteiro manual é obrigatório, não opcional.

**Pronto quando:** com o plugin ativo, o modal que abre é o do plugin (`window.__whatsbotExtensions` confirma o dono) e os 5 fluxos × 2 modos passam; com o plugin desativado, o fallback do core assume sem erro no console.

#### Status de execução — Fase C1
**Estado:** ✅ Concluída no código (2026-07-30) · ⏸️ roteiro §9.1 pendente em navegador
- **O que foi feito:** `static/TemplatePicker.js` (825 linhas) migrado para o plugin; `static/phone.js` + `phone.test.js` (cópia autossuficiente do formatador); `extends.js` reivindica `template.picker`; `plugin.yaml` **1.7.0 → 1.8.0**; espelhado em `storages/`. O `diff -u` contra o original acusa **51 linhas**, todas intencionais.
- **Como foi feito / decisões:** um **workflow de pré-voo** (4 frentes paralelas + síntese) produziu o contrato de migração ANTES da edição, e a reescrita dos 12 call sites foi feita por script (regex ancorada em `(?<![\w.])nome\(`), não à mão. Três decisões vieram de lá e **corrigiram erros meus**:
  1. **O host remontava o modal.** Eu re-resolvia o override a cada `bump()`. Como `loadPluginExtensions` limpa o registry de forma SÍNCRONA e só repovoa após N `await import()` — e `whatsapp_cloud` é o último dos 14 na ordem alfabética — um toggle de plugin trocaria o tipo do vnode **com o modal aberto**, descartando o formulário preenchido. Agora congela na montagem; a única transição permitida é "nada → alguma coisa".
  2. **IDs de DOM colidiriam na coexistência.** `datalist` casa por `id` global e rádios de mesmo `name` formam UM grupo no documento: com o fallback do core e o do plugin montados, mexer num desmarcaria o outro. Prefixados com `wac-`.
  3. **Uploads não podiam ir por `api.http`** (JSON-only ⇒ `FormData` viraria `"{}"` e o FastAPI devolveria 422). Vão por `api.services`, onde a função já vem ligada ao módulo do core.
- **Desvio do plano (antecipação consciente):** o gate `templatePickerAvailable()` foi aplicado também ao `canPickTemplate` do `NewConversationModal` — o plano o listava só na G1. É no-op hoje (o fallback existe) e evita um botão órfão depois. Os 4 desvios automáticos (`useComposer`/`useMediaUpload`) **continuam** na G1 (item 3b).
- **Problemas / pendências:** o `plugin.yaml` bumpado não reflete no banco de dev (1.7.0) porque o watcher do uvicorn só observa `*.py` — cosmético, corrige no próximo restart. Os estáticos servem a versão nova na hora (StaticFiles lê do disco).
- **Verificação:** `node --test` **369/369** (363 do core + 6 do `phone.test.js` novo) · `tests/test_endpoints.py` **1635/1635** · `--check` verde nos 5 módulos · `curl` nos 4 estáticos do plugin → **200** (`TemplatePicker.js` 39.709 b) · nenhum `../` sobrando no arquivo migrado. Verificação **adversarial** (4 lentes tentando refutar fidelidade, runtime, regressão do core e coexistência) rodando em paralelo.

---

### Fase D1 — Backend das preferências 🟢 `[paralelo com C1 — arquivos disjuntos]`

**Objetivo:** persistir favoritos e arquivados, com a permissão nova.

**Itens**
1. `[paralelo]` `migrations/001_template_prefs.sql` com as 2 tabelas de §4.2. ⚠️ Nenhum `;` dentro de comentário ([migrator.py:111-116](../plugins/migrator.py#L111)).
2. `[paralelo]` Bloco `rbac:` no [plugin.yaml](../assets/plugin_examples/whatsapp_cloud/plugin.yaml) (§4.2). Sem seeding (D3).
3. `[sequencial]` As 3 rotas em [routes.py](../assets/plugin_examples/whatsapp_cloud/routes.py), no estilo das existentes: `plugin_permission("template_archive")` só no archive; `user_id` de `request.state.user`; acesso ao banco por `make_plugin_db` + `sqlalchemy.text`.
4. `[sequencial]` `audit(...)` no archive com `resource_type="channel"` (§4.2). Import defensivo, como o que o plugin já faz ([routes.py:42-45](../assets/plugin_examples/whatsapp_cloud/routes.py#L42)).
5. `[paralelo]` `tests/` do plugin (primeiro do `whatsapp_cloud`): favoritar/desfavoritar, isolamento entre usuários, arquivar sem permissão → 403, arquivar com permissão → 200, instalação aberta → sem favoritos.

⚠️ Testes que **sobem o app** a partir de `storages/plugins/` esbarram no `_copy_plugin` ([tests/support.py:74-78](../tests/support.py#L74)), que só procura em `assets/` — é o **P2 do plano 83**. Enquanto ele não for resolvido, manter a fonte em `assets/plugin_examples/` (é onde ela está hoje) e o teste funciona.

**Pronto quando:** `GET /api/plugins/whatsapp_cloud/template-prefs?channel_id=…` responde; um usuário sem a permissão recebe 403 no archive; a chave nova aparece no `PermissionPicker` sob "Templates (WhatsApp Cloud)".

#### Status de execução — Fase D1
**Estado:** ✅ Concluída (2026-07-30)
- **O que foi feito:** `migrations/001_template_prefs.sql` (2 tabelas + 3 índices); `rbac:` no `plugin.yaml` com `template_archive` no grupo "Templates (WhatsApp Cloud)"; 3 rotas em `routes.py` (`GET /template-prefs`, `POST /template-prefs/favorite`, `POST /template-prefs/archive`); auditoria no arquivar como recurso de CANAL; 10 testes.
- **Como foi feito / decisões:**
  1. **`can_archive` como FLAG, não só como 403.** A dependency `plugin_permission` barra a rota, mas a tela precisa do booleano para ESCONDER o botão (padrão do repo). Resolvido com `server.authz.acheck` em import defensivo — sem o helper, devolve `True` e o enforcement continua sendo o da rota.
  2. **Favoritar não é auditado**, arquivar é. O guia manda não auditar preferência pessoal por usuário; arquivar é mudança de estado com dono e vai para o recurso `channel:<id>`, junto dos eventos `channel.*` do core.
  3. Escrita idempotente por `SELECT` + `INSERT` (o índice único é a autoridade), para o toggle otimista da tela não gerar 500 numa corrida.
- **Dois erros meus, pegos por verificação e não por revisão:**
  - **`;` dentro de comentário SQL** — eu escrevi o alerta sobre isso e caí nele na linha seguinte. O `_split_statements` divide em `;` sem consciência de comentário (o `';'` entre aspas escapa, o solto não), então a migration teria quebrado. Achado rodando o splitter de verdade contra o arquivo.
  - **`migrations: migrations` faltando no manifest** — sem a linha, `run_pending_migrations` retorna `[]` na primeira linha e as tabelas nunca nascem, **sem erro nenhum**. O sintoma foi silencioso: versão 1.8.0 no banco, permissão registrada, zero tabela.
- **Problemas / pendências:** os testes tiveram de ir para `tests/` do core, não para `<plugin>/tests/` — a fixture `plugin_app` vem de `tests/conftest.py` e não alcança teste fora daquela árvore. É o **P2 do plano 83**, ainda aberto; quando cair, o arquivo viaja com o zip.
- **Verificação:** migration aplicada no banco de dev (`plugin_migrations` = [1], 2 tabelas criadas, `load_error` nulo) · permissão no catálogo · **10/10** testes.

---

### Fase E1 — Favoritos na tela 🔴 `[depende de: C1, D1]`

**Objetivo:** o atendente marca os templates dele e eles sobem.

**Itens**
1. `[sequencial]` No mount do picker, buscar prefs em paralelo com a lista; falha na busca de prefs **degrada sem quebrar** (lista sem estrelas, como hoje).
2. `[sequencial]` Estrela por linha; clique otimista com rollback no erro.
3. `[sequencial]` Ordenação: favoritos primeiro, ordem da Meta preservada dentro de cada grupo.
4. `[sequencial]` Sem `user_id` (instalação aberta) a estrela não renderiza (D4).
5. `[paralelo]` Contraste no modo escuro: usar `wa-*`; o amarelo da estrela precisa passar sobre `--wa-panel` **e** `--wa-bg` nos dois temas ([themeContrast.js](../web/static/js/services/themeContrast.js)).

**Pronto quando:** favoritar, recarregar (F5) e ver a estrela mantida; entrar com outro usuário no mesmo navegador e ver a lista **sem** aquela estrela.

#### Status de execução — Fase E1
**Estado:** ✅ Concluída (2026-07-30)
- **O que foi feito:** estrela por linha (contorno/preenchida), favoritos ao topo, busca das preferências assim que o canal é conhecido, toggle otimista com rollback.
- **Como foi feito / decisões:**
  1. **O canal em modo conversa só é conhecido DEPOIS da lista** — ele vem no payload como `channel`. Por isso as preferências são um efeito à parte, disparado por `prefsChannelId`, e não uma segunda chamada no mesmo `useEffect`.
  2. **Falha ao buscar preferências degrada em silêncio**: a lista continua utilizável, só sem estrela. O que o atendente precisa é enviar template.
  3. Sem `user_id` (instalação aberta) a estrela **não renderiza** — não há a quem pertencer.
  4. A ordenação é **estável**: favoritos primeiro, mas dentro de cada grupo a ordem da Meta é preservada, senão o atendente perde a referência visual da lista.
- **Verificação:** coberta pelos testes puros de `templateFilter` (ordem estável, aba "favoritas", composição com busca).

---

### Fase E2 — Arquivar + chips de filtro 🔴 `[depende de: E1 — mesmo arquivo]`

**Objetivo:** tirar da frente os templates mortos, sem apagar nada na Meta.

**Itens**
1. `[sequencial]` `can_archive` do `GET /template-prefs` controla a visibilidade do ícone (padrão do repo: **esconder, não desabilitar**).
2. `[sequencial]` Ícone de arquivar ao lado da lixeira — **visualmente distinto** dela (o risco das duas é oposto: uma é reversível, a outra não).
3. `[sequencial]` Chips `Todas · Favoritas · Arquivadas`; o contador de arquivadas dá a pista de que existem.
4. `[sequencial]` Ordem de aplicação de §4.3 (arquivado não volta pela busca).
5. `[sequencial]` Desarquivar a partir do chip "Arquivadas".

**Pronto quando:** arquivar num navegador e ver sumir **no navegador de outro operador** após reabrir o modal; o chip "Arquivadas" devolve e permite desarquivar; sem a permissão, o ícone não existe.

#### Status de execução — Fase E2
**Estado:** ✅ Concluída (2026-07-30)
- **O que foi feito:** ícone de arquivar por linha (só com a permissão), abas `Todas · Favoritas · Arquivadas` com contadores, desarquivar pela aba, mensagens de lista vazia por aba.
- **Como foi feito / decisões:**
  1. **Ícone deliberadamente diferente da lixeira** (caixa com seta × lata de lixo): as duas ações ficam lado a lado e têm risco oposto — arquivar é reversível e local, apagar é irreversível e vai à Meta. O `title` diz isso em texto.
  2. **Abas só aparecem quando há o que separar** — sem login não há "Favoritas", e sem nada arquivado a aba seria uma lista vazia permanente.
  3. **Arquivado não volta pela busca** (só na aba "Arquivadas"). É a regra que dá sentido a "arquivar", e está travada por teste.
- **Erro meu, pego na hora:** ao trocar o gate da coluna de ações para `canArchive || canDelete`, a lixeira ficou fora do próprio gate — quem só pudesse arquivar veria o botão de apagar. Corrigido com gate próprio em `canDelete`.
- **Verificação:** testes puros de `templateFilter` (arquivado some de "todas", não volta pela busca, não aparece em "favoritas" mesmo sendo favorito, estrela não reordena a aba "arquivadas") + os 10 testes de rota (global, idempotente, por canal).

---

### Fase E3 — Busca por conteúdo 🟢 `[paralelo com E1/E2 se em commit separado]`

**Objetivo:** achar o template pelo que ele **diz**, não só pelo nome.

**Itens**
1. `[sequencial]` Predicado sobre `name`, `category`, `header.text`, `body.text`, `footer.text`, `buttons[].text`, `buttons[].url` (§2.2 confirma que tudo isso já chega).
2. `[paralelo]` Normalizar caixa **e acento** na comparação — a lição do `protocolos` foi exatamente esta (busca sem `lower`/sem acento parecendo "problema de collation" quando era bug).
3. `[paralelo]` Extrair o predicado como função pura no plugin + teste `node --test` (o plugin ganha seu primeiro teste JS).
4. `[paralelo]` Mostrar no resultado **qual trecho** casou quando o casamento foi por conteúdo — senão a linha parece um falso positivo.

**Pronto quando:** buscar uma palavra que só existe no corpo de um template e achá-lo; buscar com/sem acento dá o mesmo resultado; `node --test` do módulo puro verde.

#### Status de execução — Fase E3
**Estado:** ✅ Concluída (2026-07-30)
- **O que foi feito:** busca passa a casar nome, categoria **e conteúdo** (cabeçalho, corpo, rodapé, texto e URL de botão); quando o casamento é por conteúdo, a linha mostra **onde** casou e o trecho.
- **Como foi feito / decisões:**
  1. Extraído como módulo **puro** `static/templateFilter.js` (sem preact/rede/DOM) com **19 testes** `node --test` — a ordem de aplicação não é óbvia e é exatamente o que se quebra numa refatoração.
  2. **Normalização de acento E caixa**: buscar "cartao" acha "cartão". É a lição do `protocolos`, onde a mesma falta virou um bug que parecia problema de collation.
  3. **Trecho com o campo de origem** — sem mostrar que casou no corpo, a linha parece falso positivo (o termo não está no nome nem na categoria).
- **Erro meu, pego pelo `--check`:** o intervalo de combining marks ficou gravado como bytes literais invisíveis no arquivo; trocado pelo escape explícito `\u0300-\u036f`.
- **Verificação:** **19/19** em `templateFilter.test.js`.

---

### Fase F1 — `TemplateSpec`: tirar as regras da Meta do core 🟢

**Objetivo:** o core deixa de saber o que é `UTILITY`, `COPY_CODE` ou 16 MiB.

**Itens**
1. `[sequencial]` `TemplateSpec` + `ChannelCapabilities.template_spec` em [base.py](../channels/base.py), ao lado de `media_limits` ([:151](../channels/base.py#L151)).
2. `[sequencial]` `normalize_buttons`, `normalize_header_media`, `validate_example_upload` ([template_service.py:52-160](../app/services/template_service.py#L52)) passam a receber o spec; `template_spec=None` ⇒ **não restringe** (o erro passa a vir da Meta) + `logger.warning` uma vez por canal.
3. `[sequencial]` Mover a regex de nome (`^[a-z0-9_]+$`) das rotas para o spec.
4. `[sequencial]` `whatsapp_cloud/channels.py` declara o spec com os valores de hoje. Import defensivo (core antigo sem `TemplateSpec` continua carregando o plugin — igual ao que o plugin já faz com `MediaLimits`).
5. `[sequencial]` `_FakeTplChannel` ([test_endpoints.py:6180](../tests/test_endpoints.py#L6180)) declara um spec; os 3 checks de 400 ([:6306-6313](../tests/test_endpoints.py#L6306)) continuam verdes por outro caminho.
6. `[paralelo]` Rótulos em [permission_catalog.py:51-52](../domain/permission_catalog.py#L51) e `:143-144`: **manter** o grupo "Templates (WhatsApp Cloud)" (é o que o teste [:2402-2404](../tests/test_endpoints.py#L2402) trava e o que agrupa a chave nova do plugin).

**Pronto quando:** `grep -nE "UTILITY|COPY_CODE|16 \* 1024|application/vnd" app/services/template_service.py` não retorna nada e os 92 checks continuam verdes.

#### Status de execução — Fase F1
**Estado:** ✅ Concluída (2026-07-30)
- **O que foi feito:** `TemplateSpec` em [channels/base.py](../channels/base.py) + `ChannelCapabilities.template_spec`; `template_service.py` perdeu as 7 constantes da Meta e ganhou `spec_for()`/`validate_category()`, com os 3 validadores recebendo o spec; as 2 rotas de criação e as 2 de upload passam o spec; o `whatsapp_cloud` declara o seu; `_FakeTplChannel` declara o dele.
- **Como foi feito / decisões:**
  1. **`P2` decidida como (a): sem spec, o core NÃO restringe** — deixa passar e quem recusa é o provedor, com `logger.warning` uma vez por canal. Não recriar no core uma cópia envelhecida das regras da Meta era o ponto da fase.
  2. **A ordem de validação mudou**: a forma (categoria/cabeçalho/botões) só pode ser checada DEPOIS de saber qual é o canal, então a resolução do canal subiu. Verificado antes de mexer que nenhum teste combina input inválido com canal/conversa inexistente — a precedência observável não muda.
  3. **A validação de NOME ficou no core** (não virou campo do spec, como o plano previa): "letras, números e `_`" é regra genérica de identificador, sem vocabulário da Meta. Mover só adicionaria acoplamento.
  4. O ramo por tipo de botão deixou de usar os nomes literais da Meta como fluxo de controle: um tipo que este core não conhece cai num ramo genérico e é repassado, em vez de sumir silenciosamente.
- **Problemas / pendências:** `grep` confirma **zero** ocorrências de `TEMPLATE_CATEGORIES`, `BUTTON_TYPE_MAX`, `UPLOAD_EXAMPLE_MIMES` e irmãs no core.
- **⚠️ Achado de infraestrutura (custou 3 execuções da suíte):** o `WHATSBOT_TEST_DB_URL` aponta para um banco **compartilhado entre máquinas**. No meio desta fase apareceram falhas que pareciam regressão da F1 — primeiro no envio de vídeo, depois em contagens de RBAC, **diferentes a cada execução**. `pg_stat_activity` mostrou duas conexões de **10.8.200.103** rodando `DROP SCHEMA` + `alembic upgrade` no mesmo `whatsbot_test`. Criei `whatsbot_test_p92` (`ENCODING 'UTF8' TEMPLATE template0` — o servidor herda SQL_ASCII e o `postgres` dele nem conecta por SQLAlchemy) e a suíte deu **1635/1635**. **Qualquer execução paralela precisa de banco próprio**; ler falha de suíte sem antes checar `pg_stat_activity` leva a conclusão errada.
- **Verificação:** `tests/test_endpoints.py` **1635/1635** em banco exclusivo · `node --test` **388/388** · 10/10 do `template-prefs` · zip regerado (56.638 bytes) com o provider já declarando o spec.

---

### Fase F2 — Zip, publicação e documentação 🟢

**Itens**
1. `[sequencial]` Regenerar `assets/channel_plugins/whatsapp_cloud-plugin.zip` a partir da base decidida em A1, com bump **real** de versão.
2. `[sequencial]` Publicar em `Techify-one/whatsbot-pro-plugins` (`plugins/whatsapp_cloud/` + `catalog.json`).
3. `[paralelo]` CLAUDE.md: registrar `overrideComponent` na lista de contratos de frontend e a mudança de dono do modal na seção "Provider de canal (plugin)".
4. `[paralelo]` Instalar em produção por **Atualizar** ([plugins.py:437](../server/routes/plugins.py#L437)) — preserva tabelas, settings, migrations e o flag `enabled`.

**Pronto quando:** produção roda a versão nova, o modal é o do plugin e a permissão nova aparece na tela de Usuários.

#### Status de execução — Fase F2
**Estado:** 🟡 Parcial (2026-07-30) — **publicado**; falta o deploy em produção, que é do usuário
- **O que foi feito:** **(1)** `assets/channel_plugins/whatsapp_cloud-plugin.zip` regerado da fonte (32.771 → **56.959 bytes**), com `migrations/001_template_prefs.sql` e os 4 arquivos novos de `static/`. **(2)** Publicado em `Techify-one/whatsbot-pro-plugins@a1a2a1b` — `plugins/whatsapp_cloud/{whatsapp_cloud.zip,whatsapp_cloud.json}` + `catalog.json` (1.4.0 → 1.8.0). **(3)** CLAUDE.md ganhou a seção "Override de componente (plano 92 · B1)" com a tabela de nomes, o congelamento na montagem, o aviso do fallback congelado e a `TemplateSpec`. **(4)** A descrição do `plugin.yaml` passou a citar as três funcionalidades (é o texto que o operador lê na tela Plugins), espelhada no `.json` publicado.
- **Problemas / pendências:** **(a)** instalar em produção pelo botão **Atualizar**; **(b)** conceder `plugin.whatsapp_cloud.template_archive` aos cargos — a chave nasce sem dono (D3), então até isso ninguém arquiva.
- **Verificação:** o zip foi validado pelos parsers REAIS antes de subir — `plugins.manifest.load_manifest` (id/versão/`migrations`/screens) e `plugins.migrator._split_statements` (5 statements, nenhum `;` de comentário partindo no lugar errado). Paridade contra o publicado conferida por símbolo: **nenhuma** rota ou função da 1.4.0 sumiu na 1.8.0 (`comm -23` sobre `def`/`@router.*` dos dois `routes.py`/`channels.py`).

---

### Fase G1 — Remover o fallback do core 🔴 **RELEASE SEGUINTE**

**Objetivo:** fechar a extração — mas só depois de o zip estar rodando em produção.

**Itens**
1. `[sequencial]` Confirmar em produção que o modal ativo é o do plugin.
2. `[sequencial]` `git rm web/static/js/components/contacts/TemplatePicker.js`; o host passa a renderizar `null` sem override.
3. `[sequencial]` O gate do botão ([Composer.js:274](../web/static/js/components/contacts/Composer.js#L274)) passa a depender **só** do override — trocar `CORE_FALLBACK` para `null` no host.
3b. `[sequencial]` **(achado da B1)** Tratar os 4 desvios automáticos para o picker — [useComposer.js:219](../web/static/js/components/contacts/hooks/useComposer.js#L219)/[:334](../web/static/js/components/contacts/hooks/useComposer.js#L334) e [useMediaUpload.js:436](../web/static/js/components/contacts/hooks/useMediaUpload.js#L436)/[:457](../web/static/js/components/contacts/hooks/useMediaUpload.js#L457): sem picker, hoje eles abortariam o envio e abririam nada. Ou o host renderiza um aviso, ou os 4 consultam `templatePickerAvailable()` antes de desviar.
4. `[paralelo]` As 10 funções de template em [api.js:654-719](../web/static/js/services/api.js#L654) **ficam** — são o transporte que o plugin consome via `api.services`.

**Pronto quando:** com o plugin desativado, o botão de template **não aparece** (em vez de abrir vazio); com ele ativo, tudo funciona.

#### Status de execução — Fase G1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Ordem de deploy | Core sem picker + zip antigo = **ninguém envia template** | O fallback do core sobrevive até G1, na release seguinte (§5 Wave 5). O override sobrepõe; nunca há vácuo |
| Botão órfão | Botão que abre o nada quando o plugin está off | Gate `templatesSupported && pickerAvailable` já em B1.5 |
| Paridade do zip | Publicar a partir da base errada **regride** o plugin (aconteceu com `telegram` e `protocolos`) | Fase A1 é bloqueadora de entrega; comparar CONTEÚDO, nunca número |
| Migração sem teste | 825 linhas movidas sem cobertura automatizada | Roteiro manual A0.4 (5 fluxos × 2 modos), obrigatório em C1.4/C1.5; migração fiel em commit separado das funcionalidades |
| Corrida de boot | `extends.js` carrega depois do 1º paint | O modal só monta no clique, muito depois; host re-renderiza pelo `subscribe()` do registry |
| Atualização manual | Plugin de canal não atualiza no deploy (só `gowa` tem upgrade automático) | Documentado no plano 83 §5. F2.4 usa **Atualizar**, que preserva dados |
| Migration de plugin | `;` em comentário quebra o split | Regra explícita em D1.1 |
| Modo escuro | Estrela/chips novos ilegíveis | E1.5 + checklist final |
| Permissão sem dono | Ninguém consegue arquivar depois de publicar | Esperado (D3) — comunicar ao usuário que ele precisa conceder em Usuários → Cargos |
| Cache de 5 min | Template arquivado por outro operador só some no próximo fetch de prefs | Prefs vêm **fora** do cache do provider; recarregar o modal basta |
| `template_spec=None` | Core antigo + zip antigo = validação some silenciosamente | `logger.warning` em F1.2 + import defensivo em F1.4 |

---

## 7. O que é WhatsApp Cloud no core — inventário para D6

| Item | Onde | Destino |
|---|---|---|
| Modal + form de criação (825 linhas) | `TemplatePicker.js` | **sai** (C1 + G1) |
| Categorias, formatos, tipos/limites de botão, MIMEs, 16 MiB | [template_service.py:25-49](../app/services/template_service.py#L25) | **sai** (F1, vira `TemplateSpec`) |
| Regex do nome do template | rotas de create | **sai** (F1.3) |
| Rotas de template (11) | `conversations.py`, `channels.py` | **fica** — genérico por capability (§3.1) |
| Rótulos "(WhatsApp Cloud)" nas permissões | [permission_catalog.py:51](../domain/permission_catalog.py#L51), `:143` | **fica** — travado por teste e agrupa a chave nova |
| Códigos de erro da Meta | [message_errors.py](../server/message_errors.py) | **fica** — decisão já escrita no arquivo (ver P3) |
| `LEGACY_CLOUD_VIDEO_LIMITS` | [video_validate.py:36](../channels/video_validate.py#L36) | **fica** — plano 83 §F5 |
| ~50 menções em comentários/docstrings | vários | **fica** — zero comportamento |

---

## 8. Perguntas em aberto

**P1 — O `filter.templates.list` (seam de anotação) vale a pena além do override?**
Contexto: com D1 o plugin é dono do modal, então não precisa de filtro nenhum. Mas um 2º plugin (ex.: um "templates favoritos da equipe") não teria por onde entrar.
(a) só o override, como planejado; (b) override + um `filter.templates.list` para composição futura.
**Recomendação: (a)** — YAGNI; o override já é o contrato, e um filtro sem consumidor apodrece. ⏸️ ADIADO até existir o 2º consumidor.

**P2 — Com `template_spec=None`, o core deve não-restringir ou manter as constantes atuais como fallback legado?**
Contexto: o precedente do repo é manter fallback (`LEGACY_CLOUD_VIDEO_LIMITS`), mas isso **deixa a Meta no core** — contra D6.
(a) não-restringe + WARNING (extração total, erro vem da Meta como 502); (b) fallback legado (retrocompat total, Meta continua no core).
**Recomendação: (a)**, coerente com D6, com o WARNING tornando o caso visível. ⏸️ ADIADO para o início da F1.

**P3 — `message_errors.py` entra em D6?**
Contexto: são ~40 códigos da Meta no core, mas o arquivo **documenta** a decisão de ficar e a condição de saída ("quando um 2º provider trouxer um espaço de códigos próprio, vira `describe_status_error()` no contrato `Channel`"). Essa condição **não** foi atingida.
(a) manter (respeita a decisão escrita); (b) antecipar o gancho agora.
**Recomendação: (a)** — mover agora seria trocar um dict inerte por um gancho com um único implementador, sem ganho. ⏸️ ADIADO até o 2º provider com códigos próprios.

**P4 — Arquivar deve esconder também no "Novo atendimento"?**
Contexto: no modo channel-scoped o operador está iniciando conversa fria, onde templates de campanha antigos podem ser justamente o que ele procura.
(a) mesma regra nos dois modos (simples, previsível); (b) no modo "Novo atendimento" mostrar arquivadas por padrão.
**Recomendação: (a)** — o chip "Arquivadas" já resolve o caso raro. ⏸️ Confirmar com o usuário antes da E2.

---

## 9. Apêndice — arquivos-chave

**Core · frontend**
- [web/static/js/plugins/registry.js](../web/static/js/plugins/registry.js) — `overrideComponent` (B1.1)
- [web/static/js/plugins/api.js](../web/static/js/plugins/api.js) — exposição no `buildPluginApi` (B1.2)
- [web/static/js/components/contacts/TemplatePicker.js](../web/static/js/components/contacts/TemplatePicker.js) — host/fallback (B1.3), removido em G1
- [web/static/js/components/contacts/ContactDetail.js](../web/static/js/components/contacts/ContactDetail.js#L633) · [NewConversationModal.js](../web/static/js/components/contacts/NewConversationModal.js#L562) — montagens
- [web/static/js/components/contacts/Composer.js](../web/static/js/components/contacts/Composer.js#L274) — gate do botão

**Core · backend**
- [channels/base.py](../channels/base.py#L151) — `TemplateSpec` (F1.1)
- [app/services/template_service.py](../app/services/template_service.py#L25) — validações por spec (F1.2)
- [domain/permission_catalog.py](../domain/permission_catalog.py#L51) — rótulos (F1.6)

**Plugin `whatsapp_cloud`**
- `static/TemplatePicker.js` *(novo)* · [static/extends.js](../assets/plugin_examples/whatsapp_cloud/static/extends.js)
- [routes.py](../assets/plugin_examples/whatsapp_cloud/routes.py) — 3 rotas de prefs
- [channels.py](../assets/plugin_examples/whatsapp_cloud/channels.py#L633) — declaração do `TemplateSpec`
- [plugin.yaml](../assets/plugin_examples/whatsapp_cloud/plugin.yaml) — `rbac:` + bump
- `migrations/001_template_prefs.sql` *(novo)* · `tests/` *(novo)*

**Testes**
- [tests/test_endpoints.py:6180-6667](../tests/test_endpoints.py#L6180) — 92 checks, `_FakeTplChannel` (:6180-6250); +11 checks de mídia/janela 24h em :6668-6772
- [tests/support.py:74-78](../tests/support.py#L74) — `_copy_plugin` (limitação P2 do plano 83)

---

### 9.1 Roteiro manual de regressão do modal (produzido na A0.4)

⚠️ **Obrigatório na C1** (migração do modal) — é a única rede de segurança: não existe teste automatizado do `TemplatePicker` (verificado na A0.3). Executar **os 5 fluxos nos 2 modos**; qualquer divergência de comportamento reprova a fase.

**Pré-condições:** canal WhatsApp Cloud ativo com `waba_id` configurado; usuário logado com `conversation.reply`, `template.create` e `template.delete`.

| # | Fluxo | Passos | Resultado esperado |
|---|---|---|---|
| 1 | **Listar** | Abrir o modal | Lista carrega; cada linha tem selo de status (Aprovado/Pendente/…) e `categoria · idioma`; não-aprovado mostra "não enviável"; cabeçalho mostra "Enviando por `<canal>` · `<número>`" |
| 2 | **Buscar** | Digitar parte de um nome e de uma categoria | Filtra por nome **e** categoria (comportamento de hoje — a busca por conteúdo só entra na E3) |
| 3 | **Enviar** | Escolher um APROVADO com variável `{{1}}` | Form pede as variáveis; prévia substitui ao digitar; "Enviar" desabilitado enquanto faltar variável; ao enviar, o modal fecha e a mensagem aparece no fio |
| 3b | **Enviar (bloqueio)** | Escolher um PENDENTE | Faixa âmbar "apenas templates aprovados podem ser enviados"; botão Enviar desabilitado |
| 4 | **Criar** | "＋ Novo" → nome inválido (maiúscula/espaço) | Aviso vermelho sob o campo; "Criar template" desabilitado |
| 4b | **Criar (feliz)** | nome válido + corpo com `{{1}}` + exemplo | "Template enviado para aprovação da Meta"; volta à lista já recarregada |
| 5 | **Upload de exemplo** | "＋ Novo" → cabeçalho IMAGE → escolher JPG | "Enviando…" e depois "Pronto ✓ `<arquivo>`"; sem o handle, "Criar template" fica desabilitado |
| 6 | **Apagar** | Lixeira numa linha | Confirmação inline "Apagar / Não"; confirmando, some da lista |

**Os 2 modos:**
- **A — conversa aberta:** botão de template no compositor de uma conversa Cloud ([Composer.js:274](../web/static/js/components/contacts/Composer.js#L274)). Repetir **fora da janela de 24h** e conferir a faixa "Fora da janela de 24h: só é possível enviar um template aprovado" com o link abrindo o mesmo modal.
- **B — "Novo atendimento":** botão "Enviar como template" ([NewConversationModal.js:547-552](../web/static/js/components/contacts/NewConversationModal.js#L547)) para um número **sem conversa** naquele canal. Ao enviar, a conversa nasce e aparece na sidebar.

**Controle da C1.5 (fallback):** desativar o plugin `whatsapp_cloud` em `/plugins`, aguardar o restart e repetir os fluxos 1 e 3 — enquanto o fallback do core existir (até a G1), tudo deve continuar funcionando; após a G1, o **botão não deve aparecer**.

---

## 10. Checklist de verificação

- [ ] `venv/bin/python tests/test_endpoints.py` verde (mesmo total de checks do baseline A0, ou maior)
- [ ] Suíte rodando contra Postgres de teste (`WHATSBOT_TEST_DB_URL`, banco com `test` no nome)
- [ ] `node --test` verde no módulo puro de busca (E3.3)
- [ ] Roteiro A0.4 completo: listar · enviar · criar · upload · apagar, nos **dois** modos
- [ ] Plugin **desativado**: botão de template some (pós-G1) / fallback assume (pré-G1) — sem erro no console
- [ ] Migration do plugin aplica em banco limpo **e** em banco já com o plugin instalado
- [ ] Restart do plugin (enable/disable) sem perda de favoritos/arquivados
- [ ] Modo escuro: estrela, chips e ícone de arquivar legíveis nos dois temas
- [ ] Permissão nova aparece no `PermissionPicker` sob "Templates (WhatsApp Cloud)"; sem ela, o ícone de arquivar não renderiza
- [ ] Auditoria: arquivar gera linha com `resource_type=channel`; favoritar **não** gera linha
- [ ] Nenhum segredo em URL ou log nas rotas novas
- [ ] `grep -rn "whatsapp_cloud\|Cloud API" web/static/js/components/contacts/` sem hit de comportamento (pós-G1)
