# /new-channel — Criar um novo provider de canal (plugin) do WhatsBot

Você (Claude) vai criar um **novo provider de canal** como plugin, **sem tocar em nenhum arquivo do core**. Tudo fica em `storages/plugins/<id>/`. Um provider é um plugin mais pesado que os do `/new-plugin`: ele traz uma subclasse de `Channel` (o contrato de canal), os ganchos de **identidade de conta** (dedup — plano 32), e o **descriptor** que faz a tela Canais renderizar o form dele dinamicamente (plano 33) — sem `if provider ==` no core.

Argumento opcional do usuário (descrição do provider): `$ARGUMENTS`

## Passo 1 — Coletar requisitos

Use `AskUserQuestion` para coletar (ou inferir do `$ARGUMENTS` e confirmar com **uma** pergunta):

1. **id / provider** (snake_case, regex `^[a-z][a-z0-9_]{0,31}$`, ex: `instagram`, `messenger`, `widget`). É o nome do provider E o prefixo de tabela. **Nunca renomear depois** (quebra `usage`/canais existentes).
2. **Label + cor** para o badge/picker (`label`, ex: "Instagram"; `color` ∈ `green|blue|purple|teal|amber|orange|red|pink|gray`).
3. **Credenciais**: lista de `{key, label, type, required, placeholder?, help?}`. `type` ∈ `text | secret | token_suggest`. Ex: `[{key: access_token, label: "Access Token", type: secret, required: true}]`. Vazio = provider sem credencial (só QR/linked-device, como GOWA).
4. **Como o provider recebe mensagens** (`inbound_route`): `path` (webhook entregue no core — o mais comum), `poll` (long-poll próprio, precisa de `lifecycle.py`), ou `none`.
5. **Identidade da conta (dedup — plano 32)** — chave pra impedir a mesma conta em 2 canais. **Quando é conhecível?**
   - **No create** (está na credencial, ex: um `page_id`/`bot_token`) → implementa `identity_from_credentials` (o core dá 409 antes de persistir).
   - **Só pós-conexão** (só aparece com a sessão viva, ex: número após QR) → implementa `account_identity` (o sweep detecta e desfaz o duplicado).
   - Os dois (mesma `kind`!). Escolha o `kind` (namespace, ex: `phone`, `phone_number_id`, `bot_id`, `page_id`) e como derivar o `value` **canônico** (não-vazio; normalize variações antes).
   - Nenhum (o provider não deduplica) → deixe os dois hooks no default `None`.
6. **Capabilities**: `qr` (tem QR/linked-device?), `templates` (HSM fora de janela?), `groups`, `presence`, `reactions`, `media` (bool cada); `session_window_hours` (0 = texto livre sempre; >0 = janela tipo Cloud API).
7. **Pós-criação (UX na tela Canais)**:
   - `needs_qr` (deriva de `qr`) → abre o painel de QR pra conectar.
   - `webhook_url` → mostra uma URL de callback pra colar no provider (declare `post_create.path`, ex: `/api/webhook/<id>/{channel_id}`).
   - `autoconfigure` → o core faz POST num endpoint seu pós-criação e mostra o resultado (declare `post_create.endpoint` + opcional `webhook_path` de fallback).
   - nada.
8. **Form rico?** (opcional) — se os campos genéricos (text/secret/token_suggest/multiselect/generated) não bastam, um `form_component` (JS via `import()`). Na dúvida, **não** — o form genérico cobre a maioria.

## Passo 2 — Ler referências do core (NÃO modificar)

Antes de gerar, **leia**:

- [channels/base.py](channels/base.py) — o contrato `Channel` (métodos abstratos `status`/`send_text`/`send_media`/`parse_inbound`), `ChannelCapabilities`, `AccountIdentity`, e os docstrings de `identity_from_credentials`/`account_identity`/`reject_duplicate`/**`provider_descriptor`** (fonte da verdade da forma do descriptor)/**`contact_type`** (o tipo de contato que o canal marca).
- [channels/events.py](channels/events.py) — `InboundEvent` (o que `parse_inbound` devolve).
- [assets/plugin_examples/telegram/channels.py](assets/plugin_examples/telegram/channels.py) — melhor referência de provider credential-only por long-poll (`bot_id` derivado do token nos DOIS hooks de identidade; descriptor com `post_create.autoconfigure`).
- [assets/plugin_examples/whatsapp_cloud/channels.py](assets/plugin_examples/whatsapp_cloud/channels.py) — referência de provider por webhook + templates (identidade no create via `phone_number_id`; descriptor com `post_create.webhook_url`; janela de 24h).
- [channels/providers/gowa_channel.py](channels/providers/gowa_channel.py) — referência de provider QR/linked-device (identidade só pós-conexão via `account_identity`; descriptor com `config_fields` `generated`/`multiselect` e `needs_qr`).
- `CLAUDE.md` → seções **"Contrato de identidade de conta / dedup de canais (plano 32)"** e **"Provider de canal (plugin) — plano 33"**.

## Passo 3 — Gerar a estrutura

Crie em `storages/plugins/<id>/`:

```
storages/plugins/<id>/
├── plugin.yaml         ← manifest com entry.channels
├── __init__.py         ← vazio
├── channels.py         ← subclasse Channel + descriptor + identidade + CHANNEL_PROVIDERS
├── lifecycle.py        ← SÓ se inbound_route == 'poll' (loop de long-poll)
├── routes.py           ← SÓ se post_create.autoconfigure ou APIs próprias
├── settings.py         ← opcional (ex: api_version)
└── static/<id>.js      ← SÓ se form_component ou uma screen config:true
```

### plugin.yaml

```yaml
id: <id>
name: <Label>
version: 1.0.0
whatsbot_api_version: ">=1.0,<2.0"
description: <descrição curta>
author: <autor>
entry:
  channels: channels        # OBRIGATÓRIO — registra o provider
  lifecycle: lifecycle      # só se inbound_route == 'poll'
  routes: routes            # só se autoconfigure/APIs próprias
  settings: settings        # opcional
permissions:
  - channel.provider
  - net.outbound            # se faz HTTP externo
dependencies: []            # httpx já está no core; adicione só o que faltar
```

### channels.py (esqueleto — adapte ao provider)

```python
"""<Label> channel provider (plano 33)."""
from __future__ import annotations
import logging
from typing import Optional
import httpx
from channels.base import AccountIdentity, Channel, ChannelCapabilities, SendResult
from channels.events import InboundEvent

logger = logging.getLogger(__name__)


class <ClassName>Channel(Channel):
    provider = "<id>"

    # ── Tipo de contato (marca por canal — plano tipos-de-contato) ──
    # Todo contato materializado por este canal grava este tipo em
    # contacts.contact_type (vira marca no painel + dimensão de filtro na tela de
    # contatos). WhatsApp (GOWA/Cloud) → "whatsapp"; Telegram → "telegram". Sem
    # override herda "outros" da base. SEMPRE defina um valor coerente com o canal.
    @classmethod
    def contact_type(cls) -> str:
        return "<whatsapp|telegram|outros>"

    def __init__(self, channel_id: str, registry=None, credentials: Optional[dict] = None):
        super().__init__(channel_id, ChannelCapabilities(
            qr=<bool>, templates=<bool>, groups=<bool>, presence=<bool>,
            reactions=<bool>, media=<bool>, inbound_route="<path|poll|none>",
            session_window_hours=<int>,
            # Sem esses o canal nasce "morto" — o core rejeita criar sem eles.
            required_credentials=(<"key", ...>),
        ))
        self.registry = registry
        self._credentials = dict(credentials or {})

    # ── Descriptor (plano 33): como o core OFERECE e RENDERIZA este provider ──
    @classmethod
    def provider_descriptor(cls) -> dict:
        return {
            "provider": "<id>",
            "label": "<Label>",
            "color": "<color>",
            "credential_fields": [
                # {"key","label","type": "text|secret|token_suggest","required": bool,
                #  "placeholder"?, "help"?}
            ],
            "config_fields": [
                # não-secretos (vão pro config). type: text|multiselect|generated.
                # multiselect: {"key","label","type":"multiselect","options":[{value,label,hint?}],"default":[...]}
            ],
            "capabilities": {"needs_qr": <bool>, "templates": <bool>},
            "ai_sequential_default": <bool>,   # True só p/ linked-device (anti-block)
            "post_create": None,               # ou {"kind":"webhook_url","path":".../{channel_id}",...}
                                               # ou {"kind":"autoconfigure","endpoint":"...","webhook_path":".../{channel_id}"}
            "form_component": None,            # ou "/plugins/<id>/static/<x>.js"
        }

    # ── Credential access (P24 — nunca toque nas tabelas por SQL) ──
    def _cred(self, key: str) -> str:
        if self.registry is not None:
            try:
                v = self.registry.get_credential(self.channel_id, key)
            except Exception:
                v = None
            if v:
                return v
        return self._credentials.get(key, "") or ""

    # ── Identidade de conta (dedup — plano 32). Implemente 1 ou 2, MESMA kind ──
    @classmethod
    def identity_from_credentials(cls, creds: dict) -> Optional[AccountIdentity]:
        val = (creds.get("<cred_key>") or "").strip()   # normalize p/ forma canônica
        return AccountIdentity("<kind>", val) if val else None

    def account_identity(self) -> Optional[AccountIdentity]:
        # só pós-conexão; None enquanto desconhecido
        return None

    # ── Status ──
    def status(self) -> dict:
        # Retorne {connected, logged_in, needs_qr, error}
        return {"connected": False, "logged_in": False, "needs_qr": False,
                "error": "not_implemented"}

    # ── Outbound ──
    def send_text(self, chat_id: str, text: str, *, reply_to=None, mentions=None) -> SendResult:
        raise NotImplementedError

    def send_media(self, chat_id: str, kind: str, path_or_url: str, *, caption: str = "", filename=None) -> SendResult:
        raise NotImplementedError

    # ── Inbound ──
    def parse_inbound(self, raw: dict) -> list[InboundEvent]:
        # Traduza o payload cru → InboundEvent(kind="message"|"receipt"|"reaction", ...)
        return []


# Exportado pro loader (entry.channels → CHANNEL_PROVIDERS).
CHANNEL_PROVIDERS = [<ClassName>Channel]
```

**Regras de geração**:
- A `kind` da identidade DEVE ser consistente entre `identity_from_credentials` e `account_identity` (senão as duas nunca deduplicam entre si). O `value` é **canônico** e não-vazio (retorne `None` quando desconhecido).
- Se `inbound_route == 'poll'`, gere `lifecycle.py` com o loop de long-poll (espelhe `telegram/lifecycle.py`) e registre a task via `runtime.task`.
- Se `post_create` for `autoconfigure`, gere `routes.py` com o endpoint declarado (POST recebe `{channel_id}`) devolvendo `{ok, data:{mode, webhook_url, registered, reason}}`.
- Se `post_create` for `webhook_url`, o core já mostra a URL — nada a gerar além do descriptor.
- `required_credentials` (capabilities) e os campos `required` do descriptor DEVEM bater.
- **Sempre** implemente `contact_type()` retornando um tipo coerente com o canal (`"whatsapp"` para providers de WhatsApp, `"telegram"`, ou um tipo próprio). É o que marca cada contato criado pelo canal (`contacts.contact_type`) e alimenta o filtro por tipo. Sem override, os contatos herdam `"outros"`.
- Modo escuro: se gerar `static/<id>.js`, use classes `wa-*`/`.wa-field` (ver regra em CLAUDE.md).

## Passo 4 — Instalar + verificar

1. O plugin nasce `enabled=0`. Ative pela tela **Plugins** (ou `plugin_repo`), o que dispara restart.
2. Confirme que registrou: `GET /api/channels/providers` deve trazer o descriptor do novo provider.
3. Abra a tela **Canais → Adicionar canal**: o provider aparece no picker e o form renderiza os campos do descriptor. Crie um canal e confirme que o dedup (plano 32) barra a mesma conta 2×.
4. **NÃO** edite nada em `channels/`, `app/services/channel_service.py`, `server/routes/channels.py` nem no frontend `web/` — o core é fechado; o provider se autodescreve.
