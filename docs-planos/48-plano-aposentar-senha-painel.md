# Plano 48 — Aposentar a Senha do Painel (login legado single-password) → acesso só por email+senha (RBAC)

> **Status:** ✅ CONCLUÍDO (2026-07-15) — F0→F3 implementadas na branch `feature/aposentar-senha-painel-plano48`, abordagem **(A)** (`has_users` self-healing, fiel ao D2). Login é só email+senha (RBAC); o gate da API/WS fecha em `has_users`; o single-password foi removido (código, config keys, card, migration 0052). Suíte: **1258 passed, 8 failed pré-existentes** (busca do plugin protocolos, alheias). · **Escopo:** médio/grande (mexe em autenticação — alto risco)
> **Origem:** pedido do usuário ("já que não uso essa senha de painel"). Relacionado ao [plano 47](47-plano-trocar-propria-senha.md) (troca de senha self-service). **Método:** mapeamento exaustivo por 6 agentes paralelos + 1 crítico de completude (grep repo-wide + leitura), com `arquivo:linha` verificado e re-confirmado à mão nos pontos críticos.
> Aposenta o modo de login **single-password** ("Senha do Painel", `web_password_hash`) em favor do login **por usuário RBAC** (email+senha, Argon2 + sessão opaca), que já existe e é 100% independente do legado. **ACHADO CENTRAL (vira o risco dominante):** `rbac_enforce` **não tem writer em lugar nenhum** (não é config key, sem UI/PUT/env) ⇒ `rbac_enforced()` é **sempre False**, e o **único** gate que fecha a API hoje é `auth_required()` = ter `web_password_hash`. Logo o risco #1 **não é lockout, é PORTA-ABERTA**: numa instalação sem senha do painel (como a do usuário), `/api/*` e o `/ws` **já respondem sem token**. Este plano primeiro **fecha essa porta** (ancorando enforcement em `has_users`) e só então remove o legado.
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | Aposentar o single-password; login vira **só email+senha (RBAC)** ✅ (2026-07-15) | Remove o ramo legacy de `/api/auth/login`, `verify_token`/`generate_token`/`generate_salt`/`hash_password`, o setter `web_password` e o card no ConfigPanel. O ramo `user` (Argon2 + `user_sessions`) permanece e vira o único caminho. |
| D2 | Enforcement **ancorado em `has_users`** (`user_repo.count()>0`), self-healing — NÃO em "lembrar de ligar `rbac_enforce`" ✅ | A API fecha no exato instante em que existe ≥1 usuário; instalação genuinamente zero-user fica aberta só até o bootstrap (que é auth-exempt). Não depende de flag sem writer. |
| D3 | A correção **porta-aberta (Fase 0)** é prioridade e **vale sozinha** ✅ | Mesmo que o resto do plano escorregue, a Fase 0 deve shipar — ela corrige um buraco de segurança **pré-existente**, independente da remoção do legado. |
| D4 | **Nunca editar migrations aplicadas** (0012, 0025) ✅ | São só comentários históricos; renomear/alterar migration aplicada quebra o `alembic upgrade head` na DB viva. |
| D5 | Transição sem janela aberta: manter `auth_required` como **3ª condição OR** do gate até a fase final ✅ | Uma instalação com `web_password` setado + 0 usuários não fica momentaneamente aberta durante a migração. |
| D6 | Homônimos são intocáveis ✅ | `verify_token` de `channel_webhook.py`/`audit_repo.py` = `hub.verify_token` da Meta (Cloud API); `access_token` = credencial Techify (billing). NÃO confundir com o token legado do painel. |

---

## 1. Resumo executivo

Existem **dois esquemas de auth coexistindo** ([auth.py:1-8](../server/auth.py#L1)): (a) **legado single-password** — SHA-256 (`web_password_hash`+`salt`) → token determinístico (`generate_token`/`verify_token`), setado pelo campo "Senha do Painel"; (b) **RBAC** — Argon2id por usuário + sessão opaca em `user_sessions`. O login email+senha já é o (b) e **não toca** o legado. O problema é que o **enforcement** do backend ainda depende do legado: como `rbac_enforce` nunca liga, o middleware ([app.py:519-541](../server/app.py#L519)) e o WS ([websocket.py:22-25](../server/routes/websocket.py#L22)) só fecham `/api/*` quando `auth_required()` (= há `web_password_hash`) é True. Sem senha do painel, **a API responde sem token** ([test_endpoints.py:1363](../tests/test_endpoints.py#L1363) prova isso: `GET /api/users` → 200 sem auth, mesmo com admin criado). A solução, em 4 fases: **(0)** fechar a porta ancorando enforcement em `has_users`; **(1)** garantir o guard anti-lockout do bootstrap; **(2)** remover o código legado; **(3)** limpar storage e docs.

---

## 2. Como funciona hoje (mapa) — verificado

| Peça | Onde | Comportamento atual | Papel na aposentadoria |
|------|------|---------------------|------------------------|
| Setter da senha do painel | [config.py:139-149](../server/routes/config.py#L139) (import [:16](../server/routes/config.py#L16)) | `web_password` no PUT → `generate_salt`+`hash_password` grava `web_password_hash`/`_salt`; vazio limpa | REMOVER (único produtor server-side) |
| `has_password` (flag UI) | [config.py:114](../server/routes/config.py#L114) | `bool(web_password_hash)` no GET /api/config | REMOVER/`False` fixo |
| Hash + token legados | [auth.py:43](../server/auth.py#L43) `generate_salt`, [:48](../server/auth.py#L48) `hash_password`, [:53](../server/auth.py#L53) `generate_token`, [:63](../server/auth.py#L63) `verify_token` | Cripto do single-password (SHA-256 + token determinístico) | REMOVER as 4 funções |
| `auth_required` (gate legado) | [auth.py:73](../server/auth.py#L73) | `bool(web_password_hash)` — **hoje o ÚNICO sinal que fecha a API** | REMOVER; gate passa a `has_users` |
| `rbac_enforced` | [auth.py:78](../server/auth.py#L78) | `bool(settings.get("rbac_enforce", False))` — ⚠️ **sem writer em lugar nenhum** ⇒ sempre False | MANTER (peça do modo alvo), mas não confiar nele como gatilho |
| `resolve_request_token` | [auth.py:87](../server/auth.py#L87), ramo legacy [105-106](../server/auth.py#L105), fall-through [107](../server/auth.py#L107) | Ramo `user` (98-102) = RBAC; ramo `legacy` (105-106) aceita o token do painel | REMOVER só 105-106; **MANTER a 107** (`return None, None`) |
| Middleware `/api/*` | [app.py:519-541](../server/app.py#L519), guard [:527](../server/app.py#L527), import morto `verify_token` [:15](../server/app.py#L15) | `enforce=rbac_enforced`(=False); `auth_req=auth_required`; sem token e ambos False ⇒ guard pulado ⇒ **denied nunca avaliado ⇒ ABERTO** | ALTERAR: `enforce = rbac_enforced OR has_users (OR auth_required na transição)`; `denied = kind!='user'` |
| Gate do WebSocket | [websocket.py:22-25](../server/routes/websocket.py#L22) (import [:8](../server/routes/websocket.py#L8)) | Mesmo padrão: fecha só se `auth_required or rbac_enforced` ⇒ **WS aberto sem senha do painel** | ALTERAR igual ao middleware; exigir `kind=='user'` |
| Login | [routes/auth.py:55-72](../server/routes/auth.py#L55) (ramo user), [74-91](../server/routes/auth.py#L74) (ramo legacy), import [:11](../server/routes/auth.py#L11) | Com email → RBAC; sem email → single-password (compara hash, devolve token determinístico) | REMOVER 74-91; sem email → 400/401 |
| `/auth/check` `has_password` | [routes/auth.py:99](../server/routes/auth.py#L99), 102-114 | Modo aberto = `not has_password and not has_users`; carrega `has_password`/`has_users` p/ o AuthGate | ALTERAR: basear só em `has_users` |
| `/auth/me` ramo legacy | [routes/auth.py:132-134](../server/routes/auth.py#L132) | `kind=='legacy'` → `{user:None, legacy:True}` | REMOVER (fica inalcançável) |
| **Guard anti-lockout** | [routes/auth.py:137-154](../server/routes/auth.py#L137) `bootstrap` (auth-exempt via [app.py:469](../server/app.py#L469)); [AuthGate.js:49-57](../web/static/js/components/shell/AuthGate.js#L49) força 1º admin quando `has_users===false`; [LoginScreen.js:62-65](../web/static/js/components/LoginScreen.js#L62) loga na hora | Garante ≥1 admin antes do painel virar alcançável | MANTER/validar (é o que evita lockout) |
| Config keys legadas | [settings.py:225-226](../config/settings.py#L225) (comentário [80-82](../config/settings.py#L82)) | `web_password_hash`/`_salt` (seed-only, default "") | REMOVER da `CONFIG_KEYS` + migration que apaga as rows |
| Card "Senha do Painel" (UI) | [ConfigPanel.js:245-288](../web/static/js/components/ConfigPanel.js#L245) + payload [88-97](../web/static/js/components/ConfigPanel.js#L88) | Define/remove a senha via `web_password` | REMOVER |
| Login front | [LoginScreen.js:8-13](../web/static/js/components/LoginScreen.js#L8) (comentário legado), [api.js:1040](../web/static/js/services/api.js#L1040) `login(pw, email='')` | Form **já exige email** (código single-password inalcançável pela UI) | ALTERAR (limpeza; email obrigatório) |
| Testes do legado | [test_endpoints.py:4300-4353](../tests/test_endpoints.py#L4300) (seção "Auth — With Password"), [177-181](../tests/test_endpoints.py#L177), [199](../tests/test_endpoints.py#L199), [1362-1363](../tests/test_endpoints.py#L1362) | Exercitam set web_password + login {password} + open→200 | REESCREVER p/ bootstrap + login email+senha; 1363 vira 401 |

---

## 3. Inventário / análise

### 3.1 Núcleo (REMOVER/ALTERAR)

| # | Item | Ponto (arquivo:linha) | Ação | Risco | Esforço |
|---|------|------------------------|------|-------|---------|
| N1 | Gate do middleware → `has_users` | [app.py:519-541](../server/app.py#L519), guard [:527](../server/app.py#L527) | ALTERAR | **alto** (gate global) | M |
| N2 | Gate do WS → `has_users` | [websocket.py:22-25](../server/routes/websocket.py#L22) | ALTERAR | alto | S |
| N3 | Ramo legacy do login | [routes/auth.py:74-91](../server/routes/auth.py#L74) | REMOVER (sem email → 400/401) | médio | S |
| N4 | Ramo legacy `resolve_request_token` | [auth.py:105-106](../server/auth.py#L105) (manter 107) | REMOVER 105-106 | médio | S |
| N5 | Ramo legacy `/auth/me` | [routes/auth.py:132-134](../server/routes/auth.py#L132) | REMOVER | baixo | S |
| N6 | `has_password` em `/auth/check` | [routes/auth.py:99,102-114](../server/routes/auth.py#L99) | ALTERAR → `has_users` | médio | M |
| N7 | Funções cripto legadas | [auth.py:43,48,53,63](../server/auth.py#L43) | REMOVER 4 funções | baixo | S |
| N8 | `auth_required` | [auth.py:73](../server/auth.py#L73) | REMOVER (após gates migrarem) | médio | S |
| N9 | Setter `web_password` + `has_password` | [config.py:139-149](../server/routes/config.py#L139), [:114](../server/routes/config.py#L114), import [:16](../server/routes/config.py#L16) | REMOVER | médio | S |
| N10 | Card "Senha do Painel" + payload | [ConfigPanel.js:245-288](../web/static/js/components/ConfigPanel.js#L245), [88-97](../web/static/js/components/ConfigPanel.js#L88) | REMOVER | médio (UX) | S |
| N11 | Imports mortos `verify_token` | [app.py:15](../server/app.py#L15), [routes/auth.py:11](../server/routes/auth.py#L11) | REMOVER | baixo | S |
| N12 | Config keys legadas | [settings.py:225-226](../config/settings.py#L225) | REMOVER + migration apaga rows | baixo | M |
| N13 | Front login (email obrigatório) | [LoginScreen.js:8-13,32-48](../web/static/js/components/LoginScreen.js#L8), [api.js:1040](../web/static/js/services/api.js#L1040) | ALTERAR (limpeza) | baixo | S |
| N14 | Testes do legado | [test_endpoints.py:4300-4353](../tests/test_endpoints.py#L4300), [177-181](../tests/test_endpoints.py#L177), [199](../tests/test_endpoints.py#L199), [1362-1363](../tests/test_endpoints.py#L1362) | REESCREVER (modelo `_login` em [test_conversation_read_isolation.py:55](../tests/test_conversation_read_isolation.py#L55)) | médio | M |

### 3.2 Comentários/docstrings a atualizar (MANTER lógica, só texto)

| Ponto | O que | Ação |
|-------|-------|------|
| [auth.py:1-8](../server/auth.py#L1) | Docstring "Two coexisting schemes… Legacy single-password" | Reescrever (só RBAC) |
| [app.py:514-517](../server/app.py#L514) | Comentário do gate ("legacy… keeps working") | Atualizar |
| [api.js:1050](../web/static/js/services/api.js#L1050) | Comentário `getMe` "or legacy single-password" | Atualizar |
| [settings.py:80-82](../config/settings.py#L82), [config.py:108-110](../server/routes/config.py#L108) | Comentário `has_password (derived)` | Remover junto com o campo |
| [AuthGate.js:1-9,47](../web/static/js/components/shell/AuthGate.js#L1) | Cabeçalho + "legacy/open session" | Atualizar (é a doc do guard) |
| [authz.py:22,86](../server/authz.py#L22), [deps.py:93](../server/deps.py#L93), [users.py:3](../server/routes/users.py#L3), [saved_filters.py:23](../server/routes/saved_filters.py#L23), [saved_filter_repo.py:20](../db/repositories/saved_filter_repo.py#L20), [tables.py:806,813](../db/tables.py#L806), [permissions.js:5,11](../web/static/js/utils/permissions.js#L5), [GearMenu.js:61](../web/static/js/components/shell/GearMenu.js#L61), [PluginScreen.js:59](../web/static/js/components/PluginScreen.js#L59), [ContextMenu.js:61](../web/static/js/components/contacts/ContextMenu.js#L61), [ContactList.js:138](../web/static/js/components/contacts/ContactList.js#L138), [ContactInfoPanel.js:25](../web/static/js/components/contacts/ContactInfoPanel.js#L25) | Trocam "legacy single-password" por "modo aberto/sem identidade" — a **lógica** (default-allow quando `user is None`) **permanece** correta | Só comentário |
| [docs-planos/47-plano-trocar-propria-senha.md](47-plano-trocar-propria-senha.md) (D3) | Ressalvas de "modo legado" no endpoint self-service | Atualizar após a remoção |

### 3.3 Falsos positivos / NÃO tocar

| Item | Por quê |
|------|---------|
| `verify_token` em [channel_webhook.py](../server/routes/channel_webhook.py), [audit_repo.py:24](../db/repositories/audit_repo.py#L24) | É `hub.verify_token` da Meta (Cloud API), homônimo — remover quebra o webhook de canal |
| `access_token` config key [settings.py:227](../config/settings.py#L227) | Credencial da conta Techify (billing) — nada a ver com login |
| `hash_password_argon2`/`verify_password_argon2`/`generate_session_token` [auth.py:21,28,38](../server/auth.py#L21) | São a base do login RBAC que **permanece** |
| Migrations [0012](../db/alembic/versions/20260620_0012_rbac_users.py), [0025](../db/alembic/versions/20260625_0025_saved_conversation_filters.py) | Aplicadas — editar quebra o boot; só mencionam o legado em comentário |
| Coluna `saved_atendimento_filters.user_id` nullable | Genérica; NULL = modo aberto. Rows NULL legadas ficam "compartilhadas" (dado órfão inofensivo — ver P4) |

### 3.4 Contradição resolvida (do crítico)

- Um leitor sugeriu remover `auth.py:105-107`. **Errado**: 105-106 são o ramo legacy (removem), **107** é o `return None, None` final e **fica** — sem ela `resolve_request_token` não retorna no caminho não-autenticado. ✅ verificado à mão.
- Framing de risco: um leitor pôs **lockout** como risco #1. **Incorreto** — o login email+senha é independente do legado e o bootstrap é forçado/auth-exempt. O risco #1 real e confirmado é **porta-aberta**. A síntese abaixo prioriza porta-aberta.

---

## 4. Mudança de infraestrutura — o novo gate de enforcement

O coração do plano. Hoje (`enforce=rbac_enforced` sempre False, `auth_req=auth_required`=há senha):

```
if token or enforce or auth_req:        # sem token e sem senha ⇒ PULA ⇒ aberto
    resolve; if enforce: denied = kind!='user'
             elif auth_req: denied = kind is None   # aceita legacy OU user
             else: denied = False
```

Alvo (ancorado em `has_users`, self-healing; `auth_required` fica como 3ª condição só na transição):

```
has_users = user_repo.count() > 0                     # cacheável (ver P5)
enforce = rbac_enforced OR has_users (OR auth_required durante a transição)
if token or enforce:
    resolve; denied = (kind != 'user')                 # só sessão de usuário passa
```

- **Efeito:** com ≥1 usuário, `/api/*` e `/ws` passam a **exigir sessão de usuário** — fecha a porta. Instalação genuinamente zero-user segue aberta só até o bootstrap ([app.py:469](../server/app.py#L469) exempta `/api/auth/`).
- **Sem 2ª linha de defesa nas rotas:** `authz`/`require_permission` são default-allow quando `user is None` ([authz.py:23](../server/authz.py#L23), [deps.py:93](../server/deps.py#L93)) — **tudo** depende deste gate. Por isso o risco é alto e a Fase 0 é isolada + fortemente testada.
- Replicar **idêntico** no WS ([websocket.py:22-25](../server/routes/websocket.py#L22)).

---

## 5. Fases / Roadmap

```
WAVE 0   F0 (fechar porta-aberta: gate→has_users) 🔴   ·   F1 (validar guard anti-lockout) 🟢
              │  (barreira: o gate has_users precisa existir e estar testado)
WAVE 1   F2 (remover código legado) 🔴  [depende de: F0]
              │
WAVE 2   F3 (storage + docs) 🟢  [depende de: F2]
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|------|------|-----------|-------|-------|---------------|
| 0 | F0 | Gate middleware+WS → `has_users` + testes de enforcement | 🔴 (gate global) | alto | sem sessão + há usuários → 401 (API e WS); com sessão → 200 |
| 0 | F1 | Validar bootstrap/AuthGate (anti-lockout) + teste do fluxo | 🟢 [paralela a F0] | baixo | fresh install força 1º admin e loga; bootstrap auth-exempt ok |
| 1 | F2 | Remover login legado (backend + ConfigPanel + testes) | 🔴 [depende de F0] | médio | suíte verde sem nenhum código single-password |
| 2 | F3 | Remover config keys + migration + docs/comentários | 🟢 [depende de F2] | baixo | rows apagadas, docstrings limpas, migration round-trips |

---

### Fase 0 — Fechar a porta-aberta (ancorar enforcement em `has_users`) 🔴
**Objetivo:** `/api/*` e `/ws` exigem sessão de usuário sempre que existir ≥1 usuário — corrige o buraco de segurança **antes** de tocar no legado. Vale sozinha (D3).
**Itens:**
1. `[sequencial]` Middleware [app.py:519-541](../server/app.py#L519): computar `has_users` (via `user_repo.count()>0`, `asyncio.to_thread` — ver P5 sobre cache), gate `enforce = rbac_enforced OR has_users OR auth_required` (mantém `auth_required` nesta fase — D5), incluir `has_users`/`enforce` no guard [:527](../server/app.py#L527), `denied = kind != 'user'` quando enforce.
2. `[paralelo]` WS [websocket.py:22-25](../server/routes/websocket.py#L22): mesmo gate; `ok = (kind == 'user')` quando enforce; fechar com `code=4401`.
3. `[paralelo]` Testes (hoje **ausentes**): com admin existente e **sem token**, `GET /api/config` e `GET /api/users` → **401**; com sessão de usuário → 200. WS: sem `?token=` → close 4401; com sessão → conecta. ⚠️ [test_endpoints.py:1362-1363](../tests/test_endpoints.py#L1362) muda de 200 para 401 (é o comportamento que estava errado).
4. `[sequencial]` Confirmar exempções intactas: `/api/auth/*`, `/api/webhook/`, `/health`, deep-links SPA ([app.py:469,503-512](../server/app.py#L503)) seguem abertas sem depender de `web_password`.

**Pronto quando:** numa DB de teste com ≥1 usuário, requests sem `Authorization` a `/api/*` (não-exempt) retornam **401** e o `/ws` sem `?token=` fecha (4401); com sessão válida, 200/conecta. A suíte reflete isso (1363 agora 401). **Nenhuma linha de código legado foi removida ainda.**

#### Status de execução — Fase 0
**Estado:** ✅ Concluída (2026-07-15) — abordagem **(A) Migrar a suíte** (fiel ao D2, `has_users` self-healing). Ver reconciliação de A vs B abaixo: A foi escolhida por respeitar o D2 (enforcement ancorado em `has_users`, não em flag).
- **O que foi feito:**
  - `user_repo.has_any()` ([user_repo.py](../db/repositories/user_repo.py)) — **consulta direta** (`count() > 0`, SEM cache). ⚠️ A revisão adversarial (P5) mostrou que um cache por-processo é um **buraco de segurança**: em topologia multi-réplica/DB-compartilhado (documentada no CLAUDE.md) ou com `workers=N`, um processo irmão cacheia `False`, outro faz o bootstrap do 1º admin, e o irmão serve a API **inteira sem autenticação para sempre**. Consulta direta é correta cross-process e é barata (roda em `to_thread`). A recomendação original do plano (cache leve) foi **descartada** por esse achado.
  - Gate do middleware ([app.py](../server/app.py), bloco `/api/*`): `has_users = user_repo.has_any()`; `enforce = rbac_enforced OR has_users`; `auth_required` mantido como 3ª condição OR (D5, removida na F2); `denied = kind != "user"` quando enforce. Import `user_repo` adicionado.
  - Gate do WS ([websocket.py](../server/routes/websocket.py)): mesmo `enforce`; `ok = (kind == "user")` quando enforce; fecha `4401`.
- **Migração da suíte (A-contida):** modo aberto genuíno preservado até o 1º usuário (linha ~1206). (1) janela de membros (1206–1217) autenticada com admin **descartável** criado+removido ali, para o intervalo seguinte voltar a 0 usuários; (2) `anon = TestClient(app)` (sem header default) para as asserções "sem token → 401"; (3) **header admin default** em `client` a partir do bootstrap da seção RBAC (sessão dedicada `_suite_admin_tok`, não o `_utok` que é deslogado); (4) Seção 23 "Auth — With Password" **reescrita** para o modelo de enforcement `has_users` (+ teste do gate do WS: sem token → 4401, com sessão → conecta); (5) P17 "ai off sem operador" migrado para chamar `conversation_service.set_ai(..., actor_id=None)` direto (o caminho HTTP sempre tem operador agora); (6) comentários de "open/legacy" atualizados. Auditoria exaustiva do arquivo feita por subagente (grupos A–E) e reconciliada.
- **A vs B (decisão):** escolhida **(A)** — respeita o D2 (self-healing por `has_users`, não uma flag). O churn previsto na suíte foi contido pelo truque do **header default** (um ajuste, não "autenticar cada call site") + `anon` para os poucos casos sem-token; a semântica que muda de `None`→admin foi tratada nos pontos exatos (assign-me, /me/password, P17). O fluxo pré-bootstrap/wizard continua coberto de graça pelo `has_users` (0 usuários ⇒ aberto até o bootstrap).
- **Problemas / pendências:** `test_endpoints.py` (a suíte canônica do plano, `python tests/test_endpoints.py`) está **verde** — as 8 falhas dela são **pré-existentes/alheias** (busca acento do plugin `protocolos` instalado). **Débito de migração da suíte pytest secundária (FOLLOW-UP):** o plano escopou a migração só de `test_endpoints.py`, mas o gate `has_users` quebra qualquer arquivo pytest que **crie usuários E faça chamadas `/api/*` tokenless** (o padrão pré-48 de "modo aberto"). Migrados: `test_lifecycle_characterization` (autenticado como Operador, goldens regenerados) e `tests/endpoints/test_conversation_events_c0.py` (2 testes — `assigned_verb`/`attribute_set` — autenticados com um admin dedicado via helper `_auth_admin`; sem goldens, asserções de verbo do bus são actor-neutras; **confirmado que as falhas eram 401 do gate**). **NÃO é do plano 48**: `tests/test_utm_atendente.py` — testa o **plugin** `utm_atendente` (plano 49, instalado em `storages/`, gitignored); as 8 falhas são nos testes `selection_*`/`filter_*` que batem no **repo/DB direto (sem `/api/`)**, logo o gate `has_users` não as causa; ainda espera o próprio DB de teste (`whatsbot_test_49`) — pré-existentes/ambientais, alheias. Já compatíveis: `test_conversation_read_isolation.py` (usa `_login`), `test_rbac_characterization.py` (desenhado p/ auth — 1 falha **pré-existente/flaky**, reproduz idêntica no fork-point 661a590). Não foi adotado cache/fixture de conftest (o `teardown_module` por-arquivo funciona, mas a fixture module-autouse do conftest se mostrou não-confiável na limpeza cross-módulo — descartada).
- **Verificação:** `venv/bin/python tests/test_endpoints.py` → **1254 passed, 8 failed** (as 8 pré-existentes do protocolos). Todos os novos: `GET /api/{config,channels,users} (no token) → 401`, `/auth/check has_users=true`, `WS sem token → 4401`, `WS com sessão → conecta`, membros autenticados, `assign-me sem auth → 401`, `/me/password (no session) → 401` — **verdes**.

---

### Fase 1 — Validar o guard anti-lockout (bootstrap) 🟢 [paralela a F0]
**Objetivo:** garantir que toda instalação chega a ter ≥1 admin **antes** do painel fechar — a saída do lockout.
**Itens:**
1. `[paralelo]` Verificar (e cobrir com teste) o fluxo existente: [AuthGate.js:49-57](../web/static/js/components/shell/AuthGate.js#L49) força bootstrap quando `has_users===false`; [routes/auth.py:137-154](../server/routes/auth.py#L137) cria o 1º admin com role `admin`, só enquanto `count()==0`; `/api/auth/bootstrap` é auth-exempt ([app.py:469](../server/app.py#L469)); [LoginScreen.js:62-65](../web/static/js/components/LoginScreen.js#L62) loga na hora.
2. `[paralelo]` Teste fim-a-fim: fresh DB (0 users) → `/auth/check` diz `has_users:false` → bootstrap cria admin → login email+senha → sessão válida → `/api/*` passa.
3. `[sequencial]` (Opcional — P2) auto-migração no boot: se houver `web_password_hash` e 0 usuários, converter num admin bootstrapado (ou apenas confiar na tela de bootstrap forçada).

**Pronto quando:** partindo de 0 usuários, o painel obriga criar o admin, o bootstrap funciona sem token, e após criá-lo o gate da Fase 0 fecha a API — tudo coberto por teste.

#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-07-15)
- **O que foi feito:** confirmado que o fluxo anti-lockout já é correto para o modelo `has_users` — [AuthGate.js](../web/static/js/components/shell/AuthGate.js) força bootstrap quando `has_users===false`; `/api/auth/bootstrap` é isento ([app.py](../server/app.py)) e só cria enquanto `count()==0`; [LoginScreen.js](../web/static/js/components/LoginScreen.js) já loga por email+senha. Adicionado bloco de teste **F1** na seção "Auth" ([test_endpoints.py](../tests/test_endpoints.py)) que fixa o invariante: com **0 usuários**, `/auth/check` → `has_users=false`, `/api/config` responde **sem token** (gate aberto), `/api/auth/bootstrap` é isento (400 sem body, não 401) e o `/ws` conecta. O ciclo completo bootstrap→login→gate-fecha já é coberto pela seção RBAC (bootstrap) + Seção 23 (enforcement).
- **Como foi feito / decisões:** **P2 = (a)** — sem auto-migração `web_password`→admin no boot; o bootstrap forçado do AuthGate + a isenção de `/api/auth/*` já cobrem o lockout. (b) seria nicety opcional, dispensada.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `venv/bin/python tests/test_endpoints.py` → **1258 passed, 8 failed** (as 8 pré-existentes do protocolos). Os 4 checks F1 verdes.

---

### Fase 2 — Remover o login legado (código) 🔴 [depende de: F0]
**Objetivo:** apagar todo o single-password; login passa a ser exclusivamente email+senha.
**Itens (backend):**
1. `[sequencial]` [routes/auth.py:74-91](../server/routes/auth.py#L74): remover o ramo legacy — sem email → **400/401** ("Informe email e senha."). Limpar imports legados na [:11](../server/routes/auth.py#L11).
2. `[sequencial]` [auth.py:105-106](../server/auth.py#L105): remover o ramo `verify_token→'legacy'`; **manter a 107** (`return None, None`).
3. `[paralelo]` [routes/auth.py:132-134](../server/routes/auth.py#L132): remover o ramo `kind=='legacy'` do `/me`.
4. `[paralelo]` [auth.py:43,48,53,63](../server/auth.py#L43): remover `generate_salt`/`hash_password`/`generate_token`/`verify_token`; e `auth_required` [:73](../server/auth.py#L73) **depois** de trocar os gates para não mais precisar dela (nesta fase o gate deixa de ter a 3ª condição `auth_required` — vira só `rbac_enforced OR has_users`).
5. `[paralelo]` [config.py:139-149](../server/routes/config.py#L139) (setter `web_password`) + [:114](../server/routes/config.py#L114) (`has_password`) + import [:16](../server/routes/config.py#L16).
6. `[paralelo]` [routes/auth.py:99,102-114](../server/routes/auth.py#L99): `/auth/check` passa a decidir por `has_users` (sobre `has_password` ver P1).
7. `[paralelo]` Imports mortos `verify_token`: [app.py:15](../server/app.py#L15), [routes/auth.py:11](../server/routes/auth.py#L11).

**Itens (frontend):**
8. `[paralelo]` [ConfigPanel.js:245-288](../web/static/js/components/ConfigPanel.js#L245) + payload [88-97](../web/static/js/components/ConfigPanel.js#L88): remover o card "Senha do Painel".
9. `[paralelo]` [LoginScreen.js:8-13](../web/static/js/components/LoginScreen.js#L8) e [api.js:1040](../web/static/js/services/api.js#L1040): email obrigatório; limpar comentários (a UI já não fazia login legado).

**Itens (testes):**
10. `[sequencial]` Reescrever a seção "Auth — With Password" [test_endpoints.py:4300-4353](../tests/test_endpoints.py#L4300) para bootstrap + login email+senha (modelo `_login` em [test_conversation_read_isolation.py:55](../tests/test_conversation_read_isolation.py#L55)); ajustar [177-181](../tests/test_endpoints.py#L177) (login sem email → 400 "email obrigatório") e [199](../tests/test_endpoints.py#L199) (sobre `has_password` — P1); reconciliar [test_rbac_characterization.py:164-175](../tests/characterization/test_rbac_characterization.py#L164) (default-allow no-user).

**Pronto quando:** `grep -rn "web_password\|generate_token\|verify_token" server/auth.py server/routes/auth.py server/routes/config.py` não acha nada do legado; login sem email → 400/401; login email+senha → 200; a suíte roda **verde** no Postgres.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-07-15)
- **O que foi feito (backend):** removidas as 5 funções cripto legadas de [auth.py](../server/auth.py) (`generate_salt`/`hash_password`/`generate_token`/`verify_token`/`auth_required`); `resolve_request_token` perdeu o ramo legacy e o parâmetro `settings` (só resolve sessão de usuário) — 4 call sites atualizados; docstring reescrita. Gate do middleware ([app.py](../server/app.py)) e do WS ([websocket.py](../server/routes/websocket.py)) passaram a `enforce = rbac_enforced OR has_users` (sem a 3ª condição `auth_required`) e importam só `rbac_enforced, resolve_request_token`. `login` ([routes/auth.py](../server/routes/auth.py)) é **RBAC-only** (email+senha obrigatórios → 400 "Informe email e senha."; ramo single-password apagado); `/me` sem ramo `legacy`; `/auth/check` decide por `has_users` (`has_password` fixo `False` — P1(a)); `settings` deixou de ser lido no módulo. Setter `web_password` + import removidos de [config.py](../server/routes/config.py); `has_password` no GET vira `False` fixo. Mensagem do self-service ([account.py](../server/routes/account.py)) sem "modo de senha única".
- **O que foi feito (frontend):** card "Senha do Painel" + payload `web_password` + hooks de estado removidos de [ConfigPanel.js](../web/static/js/components/ConfigPanel.js); [LoginScreen.js](../web/static/js/components/LoginScreen.js) e `login()`/`getMe()` de [api.js](../web/static/js/services/api.js) — comentários e corpo atualizados (email obrigatório; sem fallback single-password).
- **Como foi feito / decisões:** login sem email → **400 "Informe email e senha."**; `has_password` mantido nos payloads como **False fixo** (P1(a) — sem churn no `AuthGate`/`GearMenu`, que já exibe logout por `hasPassword || currentUser` e o `currentUser` está sempre presente logado). Removido o parâmetro morto `settings` de `resolve_request_token` (limpeza).
- **Problemas / pendências:** nenhuma. Sessões legadas (token determinístico) deixam de validar — relogin por email é o esperado (comunicar no release).
- **Verificação:** `grep` não acha `web_password`/`generate_token`/`verify_token`/`generate_salt`/`hash_password`(≠argon2)/`auth_required` no core auth; nenhum import órfão; `resolve_request_token` mantém o `return None, None`. Suíte: **1258 passed, 8 failed** (pré-existentes). `test_rbac_characterization` + `test_conversation_read_isolation` + `test_audit_characterization` verdes.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída (2026-07-15)
- **O que foi feito:** removidas `ConfigKey("web_password_hash")`/`("web_password_salt")` de `CONFIG_KEYS` + comentário atualizado ([settings.py](../config/settings.py)). Migration nova **0052** ([20260715_0052_drop_web_password.py](../db/alembic/versions/20260715_0052_drop_web_password.py)) apaga as duas rows de `config` (idempotente; downgrade re-insere vazias) — **não** toca 0012/0025 (D4). Comentários stragglers (§3.2) atualizados: "legacy single-password" → "open install (no identity)" em [authz.py](../server/authz.py), [deps.py](../server/deps.py), [saved_filters.py](../server/routes/saved_filters.py), [users.py](../server/routes/users.py), [saved_filter_repo.py](../db/repositories/saved_filter_repo.py), [tables.py](../db/tables.py), [permissions.js](../web/static/js/utils/permissions.js), [GearMenu.js](../web/static/js/components/shell/GearMenu.js), [PluginScreen.js](../web/static/js/components/PluginScreen.js), [ContextMenu.js](../web/static/js/components/contacts/ContextMenu.js), [ContactList.js](../web/static/js/components/contacts/ContactList.js), [ContactInfoPanel.js](../web/static/js/components/contacts/ContactInfoPanel.js), [AuthGate.js](../web/static/js/components/shell/AuthGate.js), [ChangePasswordModal.js](../web/static/js/components/ChangePasswordModal.js).
- **Como foi feito / decisões:** `has_password` **mantido** nos payloads (False fixo) — remoção total fica para um plano futuro (P1(a)). `deps.py` "legacy _err envelope" (formato de resposta, homônimo) **não** foi tocado.
- **Problemas / pendências:** nenhuma. As 2 falhas de [test_alembic_hygiene.py](../tests/test_alembic_hygiene.py) (prefixos duplicados 0037/0042/0043/0046 + cadeia não-linear) são **pré-existentes** — confirmado removendo o 0052 e re-rodando (falham igual).
- **Verificação:** [test_postgres_roundtrip.py](../tests/test_postgres_roundtrip.py) + [test_schema_drift.py](../tests/test_schema_drift.py) **verdes** (round-trip do 0052 ok); suíte de endpoints roda `alembic upgrade head` (com 0052) e passa; `grep -rn "single-password\|web_password"` só acha migrations aplicadas (comentário histórico) + docstrings intencionais ("was retired").

---

### Fase 3 — Limpeza de storage e documentação 🟢 [depende de: F2]
**Objetivo:** remover os dados/config órfãos e a documentação que descreve o esquema inexistente.
**Itens:**
1. `[paralelo]` [settings.py:225-226](../config/settings.py#L225): remover `ConfigKey("web_password_hash")`/`("web_password_salt")` da `CONFIG_KEYS` + comentário [80-82](../config/settings.py#L82).
2. `[paralelo]` Migration Alembic nova (não-destrutiva p/ RBAC) que apaga as rows `web_password_hash`/`web_password_salt` da tabela `config`. ⚠️ **NÃO editar** as migrations 0012/0025 (D4). Rodar round-trip (upgrade/downgrade) no Postgres de teste.
3. `[paralelo]` Docstrings/comentários stragglers da tabela §3.2 ([auth.py:1-8](../server/auth.py#L1), [app.py:514-517](../server/app.py#L514), [api.js:1050](../web/static/js/services/api.js#L1050), [AuthGate.js:1-9](../web/static/js/components/shell/AuthGate.js#L1), authz/deps/users/saved_filters/tables/permissions/GearMenu/PluginScreen/ContextMenu/ContactList/ContactInfoPanel — só texto, lógica intacta).
4. `[paralelo]` Atualizar [docs-planos/47](47-plano-trocar-propria-senha.md) (D3 não precisa mais recusar "modo legado") e o exemplo `rbac_enforce=on` em [docs-planos/45](45-registro-bugs-riscos-realtime.md).

**Pronto quando:** as duas config keys somem do seed, a migration apaga as rows e faz round-trip; `grep -rn "single-password\|web_password"` no repo só acha migrations aplicadas (comentário histórico) e docs atualizadas.

#### Status de execução — Fase 3
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(migration de delete; manter has_password? — P1)_
- **Problemas / pendências:** _()_
- **Verificação:** _(migration round-trip; grep limpo; docs ok)_

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Gate global ([app.py:519-541](../server/app.py#L519)) | **alto — PORTA-ABERTA**: errar o gate abre TODA a API (não há 2ª linha nas rotas — default-allow sem user) | Fase 0 isolada 🔴 + testes "sem token → 401" (API e WS) antes de qualquer remoção; `denied = kind != 'user'` explícito |
| Janela na migração | instalação com `web_password` + 0 usuários fica aberta ao trocar o gate para `has_users` | D5: manter `auth_required` como 3ª condição OR até a Fase 2; só então removê-la |
| Lockout estreito | operador que só usava a Senha do Painel e nunca criou usuário | Guard já existe: AuthGate força bootstrap + `/api/auth/bootstrap` auth-exempt (Fase 1); comunicar no changelog |
| Sessões legadas | token determinístico deixa de validar no deploy → browsers logados por senha caem | Aceitar relogin por email (esperado); avisar no release. Login RBAC (sessão opaca) intacto |
| WS sem cobertura | gate do `/ws` tem o mesmo buraco e nenhum teste hoje | Teste de enforcement do WS na Fase 0 |
| Regressão de testes | seção 23 + 177-181 + 1363 quebram ao remover o legado | Reescrever para bootstrap+login (modelo `_login`); 1363 vira 401 |
| Homônimos (D6) | remover `verify_token` da Meta / `access_token` Techify por engano | Só mexer nos símbolos de `server/auth.py`; nunca em `channel_webhook.py`/`audit_repo.py`/`access_token` |
| Migrations aplicadas (D4) | editar 0012/0025 quebra `alembic upgrade head` na DB viva | Só migration **nova** para apagar rows; nunca alterar as aplicadas |
| Postgres | migration + suíte precisam do banco de teste | `WHATSBOT_TEST_DB_URL` (nome com `test`) |

---

## 7. Perguntas em aberto

- **P1 — `has_password` nos payloads (`/api/config`, `/api/auth/check`).** ⏸️ Contexto: só o frontend consome; após a remoção não há senha do painel. Opções: (a) **manter como `False` fixo** um release (compat do `SetupWizard`/`AuthGate`), depois remover; (b) remover já. **Recomendação:** (a) — menos churn no frontend, remove no plano seguinte.
- **P2 — Auto-migrar `web_password`→admin no boot?** ⏸️ Opções: (a) **confiar no bootstrap forçado** existente (AuthGate + `/api/auth/bootstrap`); (b) converter a senha legada num admin automaticamente. **Recomendação:** (a) — já cobre o lockout; (b) é nicety opcional.
- **P3 — Expor `rbac_enforce` (env/config) além de `has_users`?** ✅ DECIDIDO: não é necessário — `has_users` é self-healing e fecha a API no momento certo. Opcionalmente uma env `WHATSBOT_RBAC_ENFORCE` como override rígido, sem bloquear o plano.
- **P4 — Presets de filtro com `user_id IS NULL` (criados em sessão legada).** ⏸️ Ficam "compartilhados" após a migração. Opções: (a) deixar (inofensivo); (b) migration que reatribui. **Recomendação:** (a) — dado órfão sem impacto; (b) só se incomodar.
- **P5 — Custo de `user_repo.count()` por request.** ✅ DECIDIDO (2026-07-15): **(b) query direta** (`count() > 0`), SEM cache. A revisão adversarial provou que a opção (a) cache é um **buraco de segurança cross-process**: um processo irmão (multi-réplica/DB-compartilhado, ou `workers=N`) cacheia `has_users=False`, outro processo faz o bootstrap do 1º admin, e o irmão nunca invalida o próprio cache-de-processo ⇒ serve a API inteira sem token para sempre. Consulta direta é a única correta para um gate de segurança; o custo (um `count` indexado em `to_thread`) é desprezível para o perfil do painel.

---

## 8. Apêndice — arquivos-chave

**Backend (núcleo):** [server/auth.py](../server/auth.py) · [server/app.py](../server/app.py) · [server/routes/auth.py](../server/routes/auth.py) · [server/routes/config.py](../server/routes/config.py) · [server/routes/websocket.py](../server/routes/websocket.py) · [config/settings.py](../config/settings.py)
**Backend (comentários):** [server/authz.py](../server/authz.py) · [server/deps.py](../server/deps.py) · [server/routes/users.py](../server/routes/users.py) · [server/routes/saved_filters.py](../server/routes/saved_filters.py) · [db/repositories/saved_filter_repo.py](../db/repositories/saved_filter_repo.py) · [db/tables.py](../db/tables.py)
**Frontend:** [web/static/js/components/shell/AuthGate.js](../web/static/js/components/shell/AuthGate.js) · [web/static/js/components/LoginScreen.js](../web/static/js/components/LoginScreen.js) · [web/static/js/components/ConfigPanel.js](../web/static/js/components/ConfigPanel.js) · [web/static/js/services/api.js](../web/static/js/services/api.js) · [web/static/js/utils/permissions.js](../web/static/js/utils/permissions.js) + comentários em GearMenu/PluginScreen/ContextMenu/ContactList/ContactInfoPanel
**DB:** nova migration Alembic (apaga rows `web_password_*`). NÃO editar 0012/0025.
**Testes:** [tests/test_endpoints.py](../tests/test_endpoints.py) · [tests/test_conversation_read_isolation.py](../tests/test_conversation_read_isolation.py) · [tests/characterization/test_rbac_characterization.py](../tests/characterization/test_rbac_characterization.py)

---

## 9. Checklist de verificação

- [ ] **Fase 0 (segurança):** com ≥1 usuário e **sem token**, `/api/config` e `/api/users` → **401**; com sessão → 200. `/ws` sem `?token=` → close 4401; com sessão → conecta.
- [ ] Exempções intactas: `/api/auth/*`, `/api/webhook/`, `/health`, deep-links SPA seguem 200 sem token.
- [ ] Fresh install (0 usuários) força criar o 1º admin; `/api/auth/bootstrap` funciona sem token; após criar, a API fecha.
- [ ] Login **email+senha** → 200 (token opaco de sessão). Login **sem email** → 400/401. Email/senha errados → 401.
- [ ] `grep` não acha `web_password`/`generate_token`/`verify_token`/`generate_salt`/`hash_password` legados em `server/auth.py`/`routes/auth.py`/`config.py`; `resolve_request_token` mantém o `return None, None` final.
- [ ] Card "Senha do Painel" removido do ConfigPanel; nenhuma referência a `has_password` órfã no frontend.
- [ ] `verify_token` da Meta (channel_webhook) e `access_token` Techify **intactos**.
- [ ] Migration nova apaga as rows `web_password_*` e faz **round-trip** (upgrade/downgrade) no Postgres; migrations 0012/0025 **não** foram tocadas.
- [ ] Suíte **verde** no Postgres (`WHATSBOT_TEST_DB_URL`): seção "Auth — With Password" reescrita, 1363 agora 401, novos casos de enforcement (API+WS) presentes.
- [ ] Modo escuro do ConfigPanel segue legível após remover o card.
- [ ] Nenhum segredo em URL; reload/voltar-avançar não quebra o login.
