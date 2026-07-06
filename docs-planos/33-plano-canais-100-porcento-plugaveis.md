# Plano 33 — Canais 100% plugáveis (provider auto-descreve, lista/formulário dinâmicos, só GOWA bundled, comando `/new-channel`)

> **Status:** PLANEJAMENTO · **Data:** 2026-07-06 · **Escopo:** médio-grande (backend: descriptor de provider · frontend: lista + formulário dinâmicos, remover conhecimento hardcoded dos 3 providers · empacotamento: desbundlar telegram/cloud · tooling: comando `/new-channel` · docs)
> **Origem:** discussão com o usuário no plano 32 — "o frontend não deve conhecer os 3 providers; a lista de tipos de caixa na criação não deve ser hardcoded, mas vir dinamicamente do que está instalado" + "por enquanto só o GOWA vem automático; os outros eu instalo depois" + "pode ter um comando para novo canal e melhorar as docs". **Método:** leitura + `grep` do código real — afirmações com `arquivo:linha` verificado.
> **O quê/por quê:** hoje o core "sabe" GOWA/Cloud/Telegram na mão (catálogo, campos de credencial, branches `if provider ===`) — por isso os 3 eram bundled. Este plano torna canais **plugins de 1ª classe**: cada provider **se autodescreve** (o core renderiza a partir disso), telegram/cloud viram **importáveis** (só GOWA automático), e um comando **`/new-channel`** gera um provider novo correto por construção — incluindo os ganchos de identidade do **plano 32**.
> **Depende de:** **plano 32** (contrato `AccountIdentity` — o `/new-channel` gera os ganchos). Recomenda-se executar o 32 primeiro.
>
> **Como usar este plano:** preencha o "Status de execução" de cada fase ANTES de avançar. **Verde a cada fase**; **desbundlar SÓ depois** do frontend dinâmico (senão recria o código morto que motivou o plano); **um refactor por commit**.

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| **D1** ✅ (2026-07-06) | Frontend **não** conhece provider hardcoded — lista + formulário vêm do que está **instalado** (registry). | G1 (descriptor no backend) + G2 (form genérico; remover `PROVIDERS`/branches). |
| **D2** ✅ (2026-07-06) | Provider **se autodescreve**: simples → declara campos (form genérico); rico → traz componente próprio via `import()` dinâmico (mesmo padrão das screens de plugin). | Descriptor com `credential_fields` + `form_component` opcional. |
| **D3** ✅ (2026-07-06) | **Só GOWA** vem automático no bootstrap; telegram/cloud → **zips importáveis**. Instalações existentes não perdem nada. | G3 (desbundlar). `bootstrap_initial_plugins` só roda com `storages/plugins/` vazio ([loader.py:146](../plugins/loader.py)). |
| **D4** ✅ (2026-07-06) | Comando **dedicado `/new-channel`** (não enfiar no `/new-plugin`). | G4. |
| **D5** ✅ (2026-07-06) | AI settings (`config.ai`, plano 21) é **core genérico** — todo canal tem — NÃO entra no descriptor. | Form genérico sempre renderiza `AiSettingsFields`; descriptor cobre só credenciais + extras do provider. |
| **Princípio** | Nunca reintroduzir `if provider ===` no core. Descriptor = fonte única. | — |

---

## 1. Resumo executivo

O core oferece hoje um catálogo fixo de providers e um formulário de criação com ramos por provider ([constants.js:11-17](../web/static/js/components/channels/constants.js) `PROVIDERS`; [:180-210](../web/static/js/components/channels/constants.js) `buildCreatePayload` com `if provider === 'gowa'/'whatsapp_cloud'/'telegram'`; [ChannelForm.js:109,128,163](../web/static/js/components/channels/ChannelForm.js)). Isso obriga os 3 a serem bundled.

A solução: o **provider declara um descriptor** (label, cor, campos de credencial com rótulo/placeholder/segredo, flags de capacidade, e — para os ricos — um **componente de formulário próprio** carregado via `import()`). O core: (a) oferece só o que está **registrado** (`GET /api/channels/providers` estendido); (b) renderiza um **formulário genérico** dos campos do descriptor, delegando aos providers ricos (GOWA `JidTypePicker`/QR, Telegram autoconfigure) seus componentes. Com o frontend dinâmico, **desbundlar** telegram/cloud é seguro. Por fim, **`/new-channel`** gera um provider novo já com descriptor + ganchos de identidade (plano 32) + stubs de `status`/`send`/`parse_inbound`.

---

## 2. Como funciona hoje (mapa) — `arquivo:linha` verificado

### 2.1 Backend — lista de providers já é semi-dinâmica
- `GET /api/channels/providers` → `providers()` ([channel_service.py:214-223](../app/services/channel_service.py)) retorna `registrados ∩ ALLOWED_PROVIDERS` + `required_credentials`. Já **não oferece** provider não instalado. Falta: label/cor/campos/UI (o descriptor completo).
- `ALLOWED_PROVIDERS = {"gowa","whatsapp_cloud","telegram","test"}` ([channel_service.py:42](../app/services/channel_service.py)) — teto hardcoded (vira denylist/opcional em G1).
- Registro de providers: plugins via `entry.channels`/`CHANNEL_PROVIDERS` no `ChannelRegistry` ([channels/registry.py:29-46](../channels/registry.py)); `required_credentials` já é lido genericamente por capability ([channel_service.py:81-101](../app/services/channel_service.py)) — **precedente** do descriptor.

### 2.2 Frontend — conhecimento hardcoded dos 3 (o que remover)
- [constants.js:11-17](../web/static/js/components/channels/constants.js) `PROVIDERS` (label+tint); [:26-29](../web/static/js/components/channels/constants.js) `REQUIRED_CREDS_FALLBACK`; [:33-40](../web/static/js/components/channels/constants.js) `CRED_LABELS`; [:44-52](../web/static/js/components/channels/constants.js) `JID_TYPES` (GOWA).
- `buildCreatePayload`/`buildEditPayload` ([constants.js:170-230](../web/static/js/components/channels/constants.js)): branches `if provider === 'gowa'` (jid types/device id), `'whatsapp_cloud'` (4 creds), `'telegram'` (bot token).
- `ChannelForm.js`: `provider === 'gowa'` → `JidTypePicker` ([:109-124](../web/static/js/components/channels/ChannelForm.js)); `'whatsapp_cloud'` → campos de cred ([:128](../web/static/js/components/channels/ChannelForm.js)); `'telegram'` → bot token ([:163](../web/static/js/components/channels/ChannelForm.js)); `AiSettingsFields` genérico ([:178](../web/static/js/components/channels/ChannelForm.js)).
- ⚠️ Providers **ricos**: GOWA (QR connect, `JidTypePicker`, `gowa_device_id`), Telegram (`/api/plugins/telegram/autoconfigure`), Cloud (webhook health, "sugerir verify token"). Não são só "lista de campos".

### 2.3 Bootstrap — o que "bundled" significa
- `bootstrap_initial_plugins` copia `assets/plugin_examples/*` → `storages/plugins/` **só na 1ª execução (pasta vazia)** ([loader.py:146](../plugins/loader.py)). Hoje em `assets/plugin_examples/`: `gowa`, `telegram`, `whatsapp_cloud` (+ `channel_test`/`protocolos`/`runtime_probe`). Desbundlar = tirar telegram/cloud de lá e publicá-los como zip importável (padrão da Loja de Plugins).

### 2.4 Tooling — `/new-plugin` não é channel-aware
- [.claude/commands/new-plugin.md](../.claude/commands/new-plugin.md) cobre tools/screens/settings/RBAC — **zero** menção a provider de canal / `entry.channels` / `Channel` ABC. Um provider é um tipo de plugin mais pesado ⇒ comando próprio.

---

## 3. Inventário / análise

| Item | Onde | O que falta | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|
| J1 · descriptor no provider | [channels/base.py](../channels/base.py) (capability) | não existe | `provider_descriptor()` (classmethod): `{label, color, credential_fields:[{key,label,secret,placeholder,required}], config_fields?, capabilities:{needs_qr,has_autoconfigure,...}, form_component?}` | baixo | M |
| J2 · endpoint estendido | [channel_service.py:214-223](../app/services/channel_service.py) + [channels.py](../server/routes/channels.py) | só nomes+creds | `providers()` retorna o descriptor completo por provider registrado; `ALLOWED_PROVIDERS` vira denylist/opcional | baixo | S |
| J3 · form genérico | [ChannelForm.js](../web/static/js/components/channels/ChannelForm.js) + [constants.js](../web/static/js/components/channels/constants.js) | branches hardcoded | renderizar campos do descriptor; `buildCreatePayload` genérico (descriptor → credentials/config); remover `PROVIDERS`/`REQUIRED_CREDS_FALLBACK`/`CRED_LABELS`/branches | **médio-alto** | L |
| J4 · form dos providers ricos | plugins static/ | UI vive no core | GOWA/Telegram trazem componente próprio via `import()` (padrão screens); fallback = form genérico | médio | M |
| J5 · desbundlar telegram/cloud | `assets/plugin_examples/` + Loja | ainda bundled | remover de `assets/plugin_examples/`; publicar zip importável; só GOWA copiado | médio | S |
| J6 · comando `/new-channel` | `.claude/commands/new-channel.md` (novo) | não existe | scaffolds provider: `Channel` ABC + capabilities + ganchos de identidade (plano 32) + descriptor + `status`/`send`/`parse_inbound` stubs + form component opcional | médio | M |
| J7 · docs | [CLAUDE.md](../CLAUDE.md) | sem seção de provider | seção "Provider de canal (plugin)": registro, descriptor, contrato de identidade, exemplos | baixo | S |
| J8 · testes | [tests/](../tests/) + `node --test` | — | builders puros (node), endpoint descriptor, smoke criar canal via descriptor | baixo | M |

### Falsos positivos descartados
| Candidato | Por que NÃO |
|---|---|
| Mover `AiSettingsFields` pro descriptor | AI é feature **core** por-canal (plano 21) — todo canal tem `config.ai`; fica genérico no form, fora do descriptor (D5). |
| Remover `ALLOWED_PROVIDERS` já | O registry já filtra por instalado; o teto vira denylist opcional, não precisa sumir. |
| Fundir em `/new-plugin` | Provider é plugin mais pesado (ABC `Channel`); comando próprio (D4). |

---

## 4. Fases / Roadmap

```
WAVE 0
   G1(descriptor no provider)  →  G2(endpoint estendido)        ← G2 depende de G1
        └── barreira: G1+G2 alimentam G3/G4 ──┘

WAVE 1
   G3(form genérico + builders)  →  G4(form dos ricos via import)   ← G4 depende de G3
        └── barreira: front dinâmico pronto ──┘

WAVE 2  (só depois do front dinâmico)
   G5(desbundlar telegram/cloud) 🔴 · G6(/new-channel) 🟢[dep plano 32 + G1]

WAVE 3
   G7(docs) 🟢 · G8(testes) 🟢
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | G1 | backend descriptor | 🔴 | baixo | cada provider registrado devolve seu descriptor |
| 0 | G2 | endpoint | 🟢 [dep G1] | baixo | `GET /api/channels/providers` traz os descriptors |
| 1 | G3 | frontend genérico | 🔴 [dep G2] | médio-alto | criar Cloud/Telegram/GOWA sem `if provider` no core |
| 1 | G4 | forms ricos | 🟢 [dep G3] | médio | QR/jid picker/autoconfigure via componente do plugin |
| 2 | G5 | empacotamento | 🔴 [dep G3,G4] | médio | fresh install só com GOWA; telegram/cloud importáveis |
| 2 | G6 | tooling | 🟢 [dep plano 32, G1] | médio | `/new-channel` gera provider funcional |
| 3 | G7 | docs | 🟢 [dep G1,G6] | baixo | CLAUDE.md documenta provider de canal |
| 3 | G8 | testes | 🟢 [dep G3,G5] | baixo | suíte + `node --test` verdes |

---

### Fase G1 — Descriptor no provider (capability) 🔴
**Objetivo:** o provider declara tudo que o core precisa pra oferecer/renderizar, sem o core conhecê-lo.
**Itens:**
- [sequencial] Em [channels/base.py](../channels/base.py): `provider_descriptor()` (classmethod, default derivado das capabilities) devolve `{label, color, credential_fields:[{key,label,secret,placeholder,required}], config_fields?:[...], capabilities:{needs_qr,has_autoconfigure,...}, form_component?: "/plugins/<id>/static/<x>.js"}`. `required` sai do `required_credentials` existente.
- [sequencial] Implementar nos 3 providers ([gowa_channel.py](../channels/providers/gowa_channel.py), [whatsapp_cloud/channels.py](../storages/plugins/whatsapp_cloud/channels.py), [telegram/channels.py](../storages/plugins/telegram/channels.py)) espelhando o que o frontend tem hoje (labels de `CRED_LABELS`, cor de `PROVIDERS`).
**Pronto quando:** `registry.get_provider(p).provider_descriptor()` devolve, pros 3, o mesmo conjunto que o frontend hardcoda hoje.

#### Status de execução — Fase G1
**Estado:** ⬜ Não iniciada
- **O que foi feito / decisões / pendências / verificação:** _(preencher)_

---

### Fase G2 — Endpoint estendido 🟢 [dep G1]
**Objetivo:** entregar os descriptors ao frontend.
**Itens:**
- [sequencial] `providers()` ([channel_service.py:214-223](../app/services/channel_service.py)) devolve `{providers:[descriptor,...]}` por provider **registrado**; `ALLOWED_PROVIDERS` deixa de ser fonte de oferta (vira denylist opcional ou some).
**Pronto quando:** `GET /api/channels/providers` retorna descriptors só dos instalados; provider desinstalado não aparece.

#### Status de execução — Fase G2
**Estado:** ⬜ Não iniciada
- **O que foi feito / decisões / pendências / verificação:** _(preencher)_

---

### Fase G3 — Formulário genérico + builders 🔴 [dep G2]
**Objetivo:** o core renderiza qualquer provider a partir do descriptor.
**Itens (caracterização ANTES — `node --test` dos builders atuais é o contrato):**
- [sequencial] `ChannelForm.js`: renderizar `credential_fields` do descriptor genericamente (input normal/segredo por `secret`); manter `AiSettingsFields` sempre (D5). Remover os `if provider ===` ([:109,128,163](../web/static/js/components/channels/ChannelForm.js)).
- [sequencial] `constants.js`: `buildCreatePayload`/`buildEditPayload` **genéricos** — montar `credentials` a partir dos `credential_fields` e `config` a partir de `config_fields`/AI. Remover `PROVIDERS`/`REQUIRED_CREDS_FALLBACK`/`CRED_LABELS` (labels agora vêm do descriptor). `providerMeta` passa a ler o descriptor.
- [sequencial] Catálogo de criação (lista de tipos) vem de `GET /api/channels/providers` (já dinâmico), não do `PROVIDERS`.
**Pronto quando:** criar um Cloud e um Telegram só com os campos do descriptor (sem branch no core); `node --test` dos builders verde (adaptado ao genérico).

#### Status de execução — Fase G3
**Estado:** ⬜ Não iniciada
- **O que foi feito / decisões / pendências / verificação:** _(preencher)_

---

### Fase G4 — Forms dos providers ricos via `import()` 🟢 [dep G3]
**Objetivo:** GOWA/Telegram trazem seus widgets especiais sem o core saber deles.
**Itens:**
- [paralelo] Extrair `JidTypePicker` + `gowa_device_id` + fluxo QR pro componente de form do plugin `gowa` (`form_component` no descriptor), carregado via `import()` (padrão [PluginScreen]). Fallback genérico se ausente.
- [paralelo] Extrair o botão **autoconfigure** do Telegram pro componente do plugin `telegram`.
- [paralelo] Cloud (webhook health, "sugerir verify token") — decidir se vira componente próprio ou fica coberto pelo genérico + um `config_field` de ação.
**Pronto quando:** conectar GOWA (QR + jid picker) e autoconfigurar Telegram funcionam com o widget vindo do plugin; core sem conhecimento específico.

#### Status de execução — Fase G4
**Estado:** ⬜ Não iniciada
- **O que foi feito / decisões / pendências / verificação:** _(preencher)_

---

### Fase G5 — Desbundlar telegram/cloud 🔴 [dep G3,G4]
**Objetivo:** só GOWA vem automático; os outros são importáveis.
**Itens:**
- [sequencial] Remover `telegram` e `whatsapp_cloud` de `assets/plugin_examples/` (fica só `gowa` + o mínimo). Publicar os dois como `.zip` importável (padrão da Loja / repositório de versionamento).
- [sequencial] Confirmar degradação: fresh install sem eles → não aparecem na lista (G2); install existente com eles em `storages/plugins/` → intacto (bootstrap só roda com pasta vazia, [loader.py:146](../plugins/loader.py)).
- [paralelo] Atualizar CLAUDE.md (seção "Plugins bundled") para refletir "só GOWA".
**Pronto quando:** boot limpo (storages vazio) nasce só com GOWA; importar o zip do Telegram/Cloud reabilita o provider e o form dinâmico o renderiza sem mudar o core.

#### Status de execução — Fase G5
**Estado:** ⬜ Não iniciada
- **O que foi feito / decisões / pendências / verificação:** _(preencher)_

---

### Fase G6 — Comando `/new-channel` 🟢 [dep plano 32, G1]
**Objetivo:** gerar um provider de canal novo correto por construção.
**Itens:**
- [sequencial] `.claude/commands/new-channel.md`: pergunta requisitos (id, label, credenciais e rótulos, identidade da conta — **no create ou pós-conexão?** — capabilities: QR? templates? autoconfigure?); lê como referência `gowa`/`telegram`/`whatsapp_cloud` + as docstrings do contrato (plano 32 F6).
- [sequencial] Gera: classe `Channel` (provider, capabilities, `status`/`send_text`/`send_media`/`parse_inbound` stubs), os **ganchos de identidade** do plano 32 (`identity_from_credentials`/`account_identity`), o `provider_descriptor()`, o registro `entry.channels`, e (opcional) o `form_component`.
**Pronto quando:** rodar `/new-channel` gera um plugin de canal que registra, aparece na lista dinâmica, e cujo dedup (plano 32) funciona — sem tocar no core.

#### Status de execução — Fase G6
**Estado:** ⬜ Não iniciada
- **O que foi feito / decisões / pendências / verificação:** _(preencher)_

---

### Fase G7 — Docs (CLAUDE.md) 🟢 [dep G1,G6]
**Objetivo:** documentar o provider de canal como cidadão de 1ª classe.
**Itens:**
- [paralelo] Seção nova no CLAUDE.md: como registrar (`entry.channels`), o `provider_descriptor()`, o contrato de identidade (link p/ plano 32), form genérico vs `form_component`, e o comando `/new-channel`.
**Pronto quando:** um dev/IA consegue criar um provider novo lendo só o CLAUDE.md + docstrings + `/new-channel`.

#### Status de execução — Fase G7
**Estado:** ⬜ Não iniciada
- **O que foi feito / decisões / pendências / verificação:** _(preencher)_

---

### Fase G8 — Testes 🟢 [dep G3,G5]
**Itens:**
- [paralelo] `node --test` dos builders genéricos (`buildCreatePayload`/`buildEditPayload` a partir de descriptors).
- [paralelo] Endpoint: `GET /api/channels/providers` devolve descriptors; provider desinstalado ausente.
- [paralelo] Smoke: criar um canal Cloud via descriptor (sem branch) grava credenciais certas.
**Pronto quando:** `venv/bin/python -m pytest tests/ -q` + `node --test` verdes.

#### Status de execução — Fase G8
**Estado:** ⬜ Não iniciada
- **O que foi feito / decisões / pendências / verificação:** _(preencher)_

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Desbundlar antes do front dinâmico | Providers somem da lista mas o form ainda os assume → quebra | **Ordem**: G5 só depois de G3/G4 (barreira explícita). |
| Providers ricos (QR/autoconfigure) | Não cabem num "form genérico de campos" | `form_component` via `import()` (padrão screens); genérico é fallback. |
| Canal órfão | Row de telegram/cloud no banco sem o plugin instalado | Comportamento já existente (provider não registrado → status/send degradam); documentar; a UI pode sinalizar "plugin não instalado". |
| Regressão dos builders | Remover branches quebra payload | Caracterização `node --test` ANTES (G3). |
| `config` vs `credentials` | GOWA jid types/device id são `config`, não credencial | Descriptor distingue `credential_fields` × `config_fields`; ricos montam seu `config` no componente. |
| Fresh install perde telegram/cloud | Usuário novo não acha o canal | Documentar o "Importar (.zip)" na Loja; o GOWA (padrão) continua automático. |
| Modo escuro | Form/descriptor novos ilegíveis | `wa-*`/`.wa-field`, testar dark. |

---

## 6. Perguntas em aberto

**P1 — `ALLOWED_PROVIDERS` some ou vira denylist?**
⏸️ ADIADO (G2). Recomendação: oferta = providers **registrados** com descriptor; manter, no máximo, uma denylist opcional.

**P2 — Cloud vira `form_component` próprio ou fica no genérico?**
⏸️ ADIADO (G4). Recomendação: genérico + um `config_field` de ação ("sugerir verify token"); webhook health é card, não form.

**P3 — Onde mora o descriptor: capability na classe vs manifest do plugin.**
✅ DECIDIDO: **classe do provider** (capability-style, igual `required_credentials`/identidade) — funciona uniforme com o GOWA (core) e os plugins.

**P4 — `/new-channel` também gera `screen`/rotas do plugin?**
⏸️ ADIADO (G6). Recomendação: gerar o essencial (provider + descriptor + identidade + form_component opcional); rotas/screen extras ficam a cargo do `/new-plugin` se o provider precisar.

---

## 7. Checklist de verificação

- [ ] `GET /api/channels/providers` devolve descriptors só dos instalados.
- [ ] Criar Cloud/Telegram/GOWA sem nenhum `if provider ===` no core.
- [ ] `node --test` dos builders genéricos verde; `venv/bin/python -m pytest tests/ -q` verde.
- [ ] Fresh install (storages vazio) nasce só com GOWA; importar zip do Telegram/Cloud reabilita e o form dinâmico renderiza.
- [ ] Install existente com telegram/cloud em `storages/plugins/` intacto após update.
- [ ] GOWA (QR + jid picker) e Telegram (autoconfigure) funcionam com widget vindo do plugin.
- [ ] `/new-channel` gera um provider que registra, aparece na lista e deduplica (plano 32).
- [ ] CLAUDE.md documenta provider de canal + `/new-channel`.
- [ ] Formulários novos legíveis no **modo escuro**.

---

## Apêndice — arquivos-chave (por fase)

- **G1/G2:** [channels/base.py](../channels/base.py), [channels/registry.py](../channels/registry.py), [app/services/channel_service.py](../app/services/channel_service.py), [server/routes/channels.py](../server/routes/channels.py), [channels/providers/gowa_channel.py](../channels/providers/gowa_channel.py), [storages/plugins/whatsapp_cloud/channels.py](../storages/plugins/whatsapp_cloud/channels.py), [storages/plugins/telegram/channels.py](../storages/plugins/telegram/channels.py).
- **G3/G4:** [web/static/js/components/channels/ChannelForm.js](../web/static/js/components/channels/ChannelForm.js), [web/static/js/components/channels/constants.js](../web/static/js/components/channels/constants.js), [web/static/js/components/channels/JidTypePicker.js](../web/static/js/components/channels/JidTypePicker.js), [web/static/js/components/channels/QRConnect.js](../web/static/js/components/channels/QRConnect.js), plugins `static/`.
- **G5:** `assets/plugin_examples/` (remover telegram/cloud), [plugins/loader.py](../plugins/loader.py), [CLAUDE.md](../CLAUDE.md).
- **G6:** `.claude/commands/new-channel.md` (novo), ref `.claude/commands/new-plugin.md`.
- **G7:** [CLAUDE.md](../CLAUDE.md).
- **G8:** [tests/test_endpoints.py](../tests/test_endpoints.py), `web/static/js/components/channels/constants.test.*` (node).
