# Plugins auditáveis

Guia para fazer um plugin do WhatsBot registrar as próprias ações na tela
**Auditoria** (`/audit`). Vale para plugin novo e para plugin existente.

> **Regra curta**: mudou configuração ou estado que outra pessoa vai querer
> explicar depois? Chame `audit(...)`. Leu dado, listou, testou conexão? Não
> chame.

---

## 1. Por que existe um helper (e não um `if plugin ==` no core)

A trilha de auditoria do core é dirigida pelo barramento de eventos: um listener
`*` ([server/audit_listener.py](../server/audit_listener.py)) confere cada evento
emitido contra a allowlist `AUDITABLE_EVENTS`
([db/audit_actions.py](../db/audit_actions.py)) e persiste o que casar.

Essa allowlist é a **vocabulário do core** — um plugin não a edita (o core não
conhece plugin por nome, mesmo princípio dos canais e do RBAC). O que o plugin
tem é um seam:

```python
from plugins.context import audit
```

O core executa (gate global, ator, mascaramento, escrita append-only); o plugin
declara **o que** aconteceu. Adicionar um plugin auditável **não muda uma linha
do core**.

---

## 2. A API

```python
audit(plugin_id, action, *, resource_id=None, resource_type=None,
      before=None, after=None, actor_type=None, actor_label=None) -> None
```

| Parâmetro | O que é |
|---|---|
| `plugin_id` | O id do plugin. Namespaceia a ação e vira o `resource_id` default. |
| `action` | Verbo em `recurso.verbo` snake_case (`"config.update"`, `"protocolo.close"`). O core prefixa com `<plugin_id>.` → a ação gravada é `protocolos.protocolo.close`. Já vir prefixado também funciona (idempotente). |
| `resource_id` | Default = `plugin_id`. Ver §5. |
| `resource_type` | Default = `"plugin"` → a linha aparece como `plugin:<id>` na coluna **Recurso**. |
| `before` / `after` | Dicts (ou qualquer JSON) com o estado antes/depois. Viram o diff expandível ao clicar na linha. |
| `actor_type` / `actor_label` | Só para ator **não humano** (`"ai"`, `"system"`). Sem isso o ator é o usuário logado da request — automaticamente. |

Características: **fire-and-forget** (escreve fora do caminho da resposta),
**nunca levanta exceção**, e respeita o interruptor global *Registrar auditoria*
da tela Auditoria.

### Formato da ação (validado)

`^[a-z][a-z0-9_]{0,31}(\.[a-z][a-z0-9_]{0,31}){1,3}$` — id do plugin + 1 a 3
segmentos. Ação fora do formato é **descartada com WARNING no log** (não quebra a
rota). Exemplos válidos: `protocolos.config.geral`, `melhorias.sugestao.approve`,
`vendas_ia.seed.run`.

---

## 3. O padrão de call site (copie isto)

No `routes.py` do plugin:

```python
PLUGIN_ID = "meu_plugin"

# Import DEFENSIVO: o plugin é distribuído por .zip e pode cair num core anterior
# ao seam de auditoria — sem o helper ele continua funcionando, só não registra.
try:
    from plugins.context import audit as _core_audit
except ImportError:  # pragma: no cover — core antigo
    _core_audit = None


def _audit(action: str, **kw) -> None:
    """Registra uma ação deste plugin na Auditoria. Nunca quebra a rota."""
    if _core_audit is None:
        return
    try:
        _core_audit(PLUGIN_ID, action, **kw)
    except Exception:  # noqa: BLE001 — auditoria nunca derruba a ação auditada
        pass
```

E em cada rota que muda algo:

```python
@router.put("/config", dependencies=[plugin_permission("config")])
async def set_config(body: dict):
    before = logic.get_config()          # 1. snapshot ANTES
    data = logic.set_config(body or {})  # 2. a ação real
    _audit("config.update", before=before, after=data)   # 3. registra
    return {"ok": True, "data": data}
```

Três regras de ouro:

1. **Snapshot do `before` antes da escrita** — depois já é tarde.
2. **Audite DEPOIS do sucesso** — nunca antes de um `return _err(...)`. Uma
   tentativa que falhou não é uma mudança.
3. **Uma linha por ação do usuário** — não uma por row tocada.

---

## 4. O que auditar (e o que não)

| Audite | Não audite |
|---|---|
| Configuração do plugin (telas `config:true`, endpoints `/config`) | Qualquer `GET` / listagem / busca |
| Mudança de estado com dono (fechar, reabrir, atribuir, aprovar, recusar) | Teste de conexão, ping, health |
| Escrita em recurso **do core** (agentes, prompts, tools, variáveis, tags) | Preferência pessoal por-usuário (o "meu filtro" de cada um) |
| Criar/editar/excluir objeto compartilhado (visualização de equipe, campo) | Mensagem de chat / evento de alto volume |
| Ação que dispara efeito externo (semear agentes, re-login de executor) | Cache, invalidação, retry técnico |
| **Entregar um segredo em claro** ao operador (ver §6) | Tráfego de cliente final (widget, visitante) |

A última linha é a exceção deliberada ao "GET não audita": uma rota que REVELA um
segredo (ex.: `GET /reveal-hmac` do plugin `website`) registra **quem viu** — o
valor, obviamente, nunca entra na linha.

Critério prático: **se alguém puder perguntar "quem mexeu nisso?" daqui a três
meses, tem que estar na trilha.** Se a resposta for "ninguém liga", fora.

### Conversa NUNCA entra na trilha

**Regra dura**: a Auditoria registra *mudança de configuração e de estado
administrativo*. **Mandar ou receber mensagem num canal não gera linha nenhuma** —
nem o envio do operador, nem a resposta da IA, nem o inbound do cliente, nem
reação/edição/recibo/presença. O histórico de `messages` já é o registro disso, e
uma linha por mensagem afogaria a tela em minutos.

Por isso ficam **fora** da allowlist do core, de propósito: `message.sent`,
`message.received`, `message.saved`, `message.persisted`, `message.reaction`,
`message.edited`, `message.revoked`, `message.deleted`, `message.failed`,
`presence.changed`, `receipt.changed` e `channel.status_changed` (um *read* que
roda a cada poll do painel).

Dois testes travam isso: `test_audit_ignores_message_traffic` dirige um webhook
inbound + um envio do operador e exige `audit_log` **intacta**; e
`test_audit_message_events_stay_out_of_allowlist` falha se alguém puser um desses
eventos na allowlist ([tests/integration/characterization/test_audit_characterization.py](../tests/integration/characterization/test_audit_characterization.py)).

Num plugin, a mesma regra: não audite o endpoint que recebe a mensagem do
visitante (é o que o `website` faz com as rotas `/public/*`) nem o chat agêntico
do `melhorias`. Audite a *configuração* do canal — para onde o webhook aponta,
qual token, quem é membro.

### Volume

A trilha é append-only e tem retenção por `purge()`. Um endpoint chamado a cada
mensagem **não** entra (ver acima). Na dúvida sobre um endpoint de operação:
pergunte quantas linhas ele produz num dia movimentado — se for da ordem do
número de mensagens, fora.

---

## 5. `resource_id`: plugin ou entidade?

O default (`resource_id = plugin_id`) faz o filtro **ID do recurso =
`protocolos`** listar *tudo* daquele plugin — geralmente é o que o auditor quer.
Identifique a entidade dentro de `after`:

```python
_audit("protocolo.set_field",
       after={"protocolo_id": atid, "key": key, "value": value})
```

Troque `resource_id` só quando a granularidade por entidade valer mais que a
visão agregada — o filtro é de igualdade exata, então você perde o "tudo do
plugin" ao fazer isso.

### Plugin de CANAL: grave no canal, não no plugin

Um plugin **provider de canal** (gowa, telegram, whatsapp_cloud, website,
facebook_messenger, instagram) é a exceção recomendada. Suas ações são sempre
*sobre um canal*, e o core já grava `channel.create/update/delete/...` com
`resource_type="channel"` + `resource_id=<channel_id>`. Alinhe-se a isso:

```python
def _audit(action: str, channel_id: str, **kw) -> None:
    _core_audit(PLUGIN_ID, action, resource_type="channel",
                resource_id=channel_id, **kw)
```

Assim **um filtro por canal devolve a história inteira dele** — criado, editado,
desconectado (core) *e* webhook redirecionado, Página assinada (plugin). A ação
continua namespaceada (`telegram.webhook.set`), então dá para saber quem fez o
quê. Config do plugin que **não** é por canal (ex.: o alerta de desconexão do
`gowa`, que é global) mantém o default `plugin:<id>`.

---

## 6. Segredos: o que NUNCA vai para a trilha

O core mascara (`***`) valores cujas **chaves** casam a denylist de
[db/repositories/audit_repo.py](../db/repositories/audit_repo.py) (`api_key`,
`token`, `password`, `secret`, `credentials`, …). Isso é uma rede de segurança,
**não** uma licença: uma chave de nome inocente (`proxy_url`, `dsn`,
`page_id`) passa em claro.

Regra do plugin: **não coloque o segredo no payload.** Registre que ele mudou:

```python
after = {"ai_server_url": url, "ai_server_secret_definido": bool(secret)}
```

Mesma decisão que o core toma nos canais: o snapshot leva a *lista de chaves de
credencial preenchidas*, nunca os valores.

Também não duplique conteúdo que já é **versionado** em outro lugar. Quando o
plugin `melhorias` grava um prompt de agente, a trilha guarda `{key, version,
change_note}` — o texto vive em `ai_agents_history`, com Reverter na UI. Duas
cópias divergem; um ponteiro, não.

---

## 7. Ator: humano, IA ou sistema

Por padrão o ator sai de um `ContextVar` que o middleware de auth preenche com o
usuário logado — a rota do plugin não precisa passar `current_user`.

Force o ator só quando o autor **não é** o humano da request:

```python
# Um executor externo (autenticado por HMAC) aplicando mudanças propostas pela IA.
audit("melhorias", "ia.agent_save", "roteador",
      actor_type="ai", actor_label=f"Executor de melhorias (por {quem})",
      after={"key": "roteador", "version": nova_versao})
```

Rotas sob `/public/` são auth-exempt: o `ContextVar` está vazio ali, então o ator
cairia em `system`. Marque explicitamente `ai`/`system` e registre no payload
quem autorizou (`on_behalf_of_user_id`) — quem liberou fica registrado, sem
fingir que foi ele quem digitou a mudança.

---

## 8. Plugin só com settings declarativas: nada a fazer

Se toda a configuração do plugin é `settings.py` (`class Settings(BaseModel)`), o
core **já audita**: o `PUT /api/plugins/<id>/settings` emite
`plugin.settings.changed`, que vira a ação `plugin.settings_update` com o diff
antes/depois dos valores. É o caso do plugin `guarda_ia`.

O helper é para o que o core não vê: os endpoints REST do próprio plugin.

---

## 9. Onde a linha aparece

Tela **Auditoria** (engrenagem → Auditoria), gated por `audit.read`:

| Coluna | De onde vem |
|---|---|
| Data/hora | epoch da escrita |
| Ator | usuário logado (badge Usuário/Sistema/IA) |
| Ação | `<plugin_id>.<recurso>.<verbo>` |
| Recurso | `plugin:<plugin_id>` |
| IP | IP da request |
| (linha expandida) | diff **Antes** × **Depois** + `request_id` |

Os selects **Recurso** e **Ação** são populados por `SELECT DISTINCT` sobre a
tabela: a ação nova aparece no filtro **assim que a primeira linha for gravada**
— não há catálogo para registrar. Exportação CSV/JSON respeita os filtros.

---

## 10. Checklist antes de publicar o plugin

- [ ] `PLUGIN_ID` + helper `_audit` com import defensivo no `routes.py`.
- [ ] Toda rota `POST`/`PUT`/`PATCH`/`DELETE` decidida: audita ou justificado por que não.
- [ ] `before` capturado antes da escrita; `_audit` depois do sucesso.
- [ ] Nenhum segredo, token ou conteúdo versionado dentro de `before`/`after`.
- [ ] Ação bate a regex e é legível em português no diff.
- [ ] Testado: fez a ação na UI e a linha apareceu em `/audit` com o seu usuário.

## 11. Referências no código

| Quero ver | Arquivo |
|---|---|
| O helper | [plugins/context.py](../plugins/context.py) → `audit()` |
| O write path + gate global | [server/audit_listener.py](../server/audit_listener.py) → `record()` |
| Vocabulário do core + regex | [db/audit_actions.py](../db/audit_actions.py) |
| Repo append-only + mascaramento | [db/repositories/audit_repo.py](../db/repositories/audit_repo.py) |
| Config + operação (exemplo completo) | `storages/plugins/protocolos/routes.py` |
| Ator `ai` + on-behalf-of | `storages/plugins/melhorias/internal_routes.py` |
| Ação única de alto impacto | `storages/plugins/vendas_ia/routes.py` → `/seed` |
| Plugin de canal (`channel:<id>`) | `storages/plugins/telegram/routes.py`, `.../whatsapp_cloud/routes.py` |
| Config global de plugin de canal | [assets/plugin_examples/gowa/routes.py](../assets/plugin_examples/gowa/routes.py) → `/alert-settings` |
| Segredo revelado (exceção ao GET) | `storages/plugins/website/routes.py` → `/reveal-hmac` |

---

## Apêndice — resumo migrado do `CLAUDE.md` (plano 139)

> O `CLAUDE.md` carrega hoje só a regra curta e o link para este guia. O texto abaixo é o
> que ele trazia antes do corte — o contrato do seam `audit()`, o write path e as regras
> de escopo. Mantido aqui verbatim para não se perder.

### Auditoria de plugins

A trilha (`audit_log`, tela `/audit`) é dirigida pelo bus: o listener `*` ([server/audit_listener.py](../server/audit_listener.py)) confere cada evento contra a allowlist `AUDITABLE_EVENTS` ([db/audit_actions.py](../db/audit_actions.py)). Essa allowlist é a **vocabulário do CORE** — plugin não a edita (o core não conhece plugin por nome, mesmo princípio dos canais/RBAC). O plugin registra as próprias ações pelo seam `audit()`:

```python
from plugins.context import audit
audit("protocolos", "config.geral", before=antes, after=depois)
# → ação  protocolos.config.geral   recurso  plugin:protocolos   ator: usuário logado
```

- **Contrato** ([plugins/context.py](../plugins/context.py) `audit()`): a ação é namespaceada com o id do plugin (`namespaced_action`) e validada contra `PLUGIN_ACTION_RE` (`<plugin_id>.<recurso>.<verbo>`; fora do formato ⇒ WARNING e a linha é descartada, a rota segue). `resource_type` default `"plugin"`, `resource_id` default = id do plugin (o filtro "ID do recurso" lista tudo daquele plugin). Fire-and-forget, nunca levanta, respeita o master `audit_enabled`. O ator sai do `ContextVar` da request (o usuário logado) — `actor_type="ai"/"system"` só para autor não-humano (executor externo, job).
- **Write path** ([server/audit_listener.py](../server/audit_listener.py) `record()`): o ÚNICO caminho de escrita fora do listener. Aplica o gate global + resolução de ator; um ator forçado (`ai`) não herda id/rótulo do humano da request.
- **Segredo nunca entra**: o `audit_repo` mascara por NOME de chave (rede de segurança, não licença) — o plugin registra `{"secret_definido": True}`, não o valor. Conteúdo já versionado (prompt de agente, código de tool) entra como PONTEIRO (`{key, version}`), não como cópia.
- **Plugin de CANAL grava no CANAL**: um provider (gowa/telegram/whatsapp_cloud/website/facebook_messenger/instagram) passa `resource_type="channel", resource_id=<channel_id>` — as ações dele são sobre um canal, e assim caem no MESMO recurso dos eventos `channel.*` do core: **um filtro por canal devolve a história inteira** (criado/editado/desconectado pelo core + webhook redirecionado/Página assinada pelo plugin). Config que não é por canal (ex.: o alerta de desconexão do `gowa`, global) mantém o default `plugin:<id>`.
- **Settings declarativas já são auditadas** pelo core (`plugin.settings.changed` → `plugin.settings_update`, com diff): plugin que só tem `settings.py` (ex.: `guarda_ia`) não precisa de nada.
- **O que auditar**: configuração, mudança de estado com dono (fechar/atribuir/aprovar), escrita em recurso do core, ação com efeito externo. **O que não**: GET/listagem, teste de conexão, preferência pessoal por-usuário, evento de alto volume.
- **CONVERSA NUNCA ENTRA NA TRILHA** (regra dura): enviar/receber mensagem num canal não gera linha nenhuma — nem envio do operador, nem resposta da IA, nem inbound do cliente, nem reação/edição/recibo/presença. O histórico de `messages` já é esse registro. Ficam fora da allowlist de propósito: `message.*`, `presence.changed`, `receipt.changed` e `channel.status_changed` (read que roda a cada poll). Travado por `test_audit_ignores_message_traffic` (webhook inbound + envio do operador ⇒ `audit_log` intacta) e `test_audit_message_events_stay_out_of_allowlist`.
- Guia completo + checklist: [docs/PLUGINS_AUDITAVEIS.md](../docs/PLUGINS_AUDITAVEIS.md). Plugins que já usam: `protocolos` (config + operação), `melhorias` (aprovações + executor com ator `ai`), `vendas_ia` (`/seed`), e os 6 providers de canal (`gowa` alerta de desconexão; `telegram`/`whatsapp_cloud`/`facebook_messenger`/`instagram` webhook+assinatura; `website` revelação do segredo HMAC).
