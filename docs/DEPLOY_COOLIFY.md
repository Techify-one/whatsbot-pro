# Deploy no Coolify — persistência de plugins, sessão do WhatsApp e mídia

> **TL;DR:** o Coolify deploya pelo `Dockerfile`, que **de propósito não declara
> `VOLUME`**. Sem configurar **Persistent Storage** na UI do Coolify, todo
> **redeploy recria o container com o disco zerado** — os plugins instalados
> somem da interface, a sessão do WhatsApp cai (pede QR de novo) e a mídia enviada
> vira "indisponível". O banco Postgres (externo) sobrevive; só o **disco** se
> perde.

## Por que isso acontece

- O deploy é `git push` → o Coolify clona o repo e builda o [`Dockerfile`](../Dockerfile).
- `storages/` e `statics/` estão no `.dockerignore` e nascem **vazios** na imagem.
- Um `VOLUME` no Dockerfile criaria um volume **anônimo** que o Coolify **descarta**
  ao recriar o container — por isso ele é omitido de propósito
  ([Dockerfile:51-58](../Dockerfile#L51-L58)).
- No boot, só o plugin `gowa` é re-semeado de `assets/plugin_examples/`; **qualquer
  outro plugin não volta sozinho**.

O que **sobrevive** a um redeploy (está no Postgres externo): as **configurações**
dos plugins (`config` chaves `plugin.<id>.*`), os **dados** (`plugin_<id>_*`), o
histórico de migrations e as linhas de `plugins`. O que **se perde** (está no disco
do container): o **código** dos plugins em `storages/plugins/<id>/`, a **sessão do
WhatsApp/GOWA** em `storages/` e a **mídia enviada** em `statics/senditems/`.

## O que fazer (uma vez, na UI do Coolify)

No recurso do WhatsBot → **Storages / Persistent Storage**, adicione dois mounts
para volume/host-path persistente:

| Mount no container | Cobre |
|---|---|
| `/app/storages` | código dos plugins (`storages/plugins/`) **+** sessão do WhatsApp/GOWA |
| `/app/statics` (ou ao menos `/app/statics/senditems`) | mídia enviada pelo operador |

Isso replica o que o [`docker-compose.yaml`](../docker-compose.yaml) já faz por
bind mount (`./data/storages`, `./data/statics`). Feito isso, um redeploy passa a
preservar tudo — o bootstrap deixa de reinicializar a pasta porque ela não está
mais vazia.

## Salvaguarda automática no boot

O app faz um **auto-check de persistência** no boot
([`server/persistence_check.py`](../server/persistence_check.py)): grava um
token-sentinela num arquivo dentro de `storages/` **e** na tabela `config` do
Postgres. Se num boot o token do banco existe mas o do disco sumiu, o disco foi
zerado → o app **grita nos logs**:

```
persistence-check: storages/ NÃO é persistente! ... Configure Persistent Storage
no Coolify mapeando /app/storages e /app/statics ...
```

Se estiver tudo certo, o log diz `storages/ persistente (marca confere)`.

**Como conferir sem caçar log:** `GET /api/admin/database` (requer permissão
`database.manage`) retorna `storage_persistent` (`true`/`false`/`null`) e
`storage_persistence_status` (`persistent` / `ephemeral` / `initialized`).

## Verificação pós-configuração

1. Configure os dois mounts acima e faça **um** redeploy.
2. Nos logs do boot, confirme `storages/ persistente` (ou `marca inicializada` no
   1º boot com o volume).
3. Faça um **segundo** redeploy e confirme que os plugins continuam na tela
   Plugins e que o WhatsApp segue conectado (sem pedir QR).

## Recuperar plugins perdidos num redeploy anterior

Como as configs/dados seguem no Postgres, **re-importe o mesmo `.zip` (mesmo id)**
de cada plugin pela tela Plugins → **Importar (.zip)** e habilite — as configs e
dados re-vinculam sozinhos (migrations já aplicadas são puladas). Para saber o que
restaurar, no banco de prod:

```sql
SELECT id, version, enabled FROM plugins ORDER BY id;
SELECT key, value FROM config WHERE key LIKE 'plugin.%' ORDER BY key;
```

Fonte dos `.zip`: o repositório `whatsbot-pro-plugins` (`plugins/<id>/<id>.zip`).
Configure o Persistent Storage **antes** de recuperar, senão os plugins somem de
novo no próximo redeploy.
