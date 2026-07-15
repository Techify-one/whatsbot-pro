# Plano 47 — Cada usuário RBAC troca a própria senha (self-service, sem permissão)

> **Status:** PLANEJAMENTO · **Data:** 2026-07-15 · **Escopo:** pequeno/médio
> **Origem:** pedido do usuário ("a Atendente poder trocar a senha dela"). Um atendente sem `users.manage` hoje não tem NENHUMA forma de trocar a própria senha — só um admin reseta. **Método:** leitura do código real (auth/RBAC/middleware/frontend) + `grep`, com `arquivo:linha` verificado.
> Cria uma troca de senha **self-service** para qualquer usuário RBAC logado, exigindo a senha atual (re-autenticação). É uma **capacidade universal da conta**, NÃO uma permissão RBAC: gatear "trocar a própria senha" criaria o anti-padrão de um usuário que não pode rotacionar a própria credencial. Fluxo distinto de dois já existentes: o **"Resetar senha"** de admin (gated por `users.manage`, reseta a senha de OUTROS) e a **"Senha do Painel"** legada (single-password compartilhada em Configurações Gerais).
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | Trocar a própria senha **NÃO** vira permissão RBAC ✅ (2026-07-15) | Nenhuma entrada nova em `PERMISSION_CATALOG`. Gate = apenas "ter identidade RBAC na sessão" (`current_user` != None). |
| D2 | Exige a **senha atual** (re-autenticação) ✅ (2026-07-15) | O endpoint verifica `current_password` antes de gravar a nova. Bloqueia troca via sessão sequestrada. |
| D3 | Vale **só para usuários RBAC** (email+senha) ✅ (2026-07-15) | Sem identidade de usuário o endpoint recusa com 403. _(Nota: o [plano 48](48-plano-aposentar-senha-painel.md) aposentou o single-password; o 403 agora só ocorre em instalação aberta antes do 1º admin.)_ |
| D4 | Fluxo **separado** do "Resetar senha" de admin e da "Senha do Painel" ✅ (2026-07-15) | Novo endpoint + nova UI próprios; NÃO reaproveita `/api/users/{id}/password` nem o campo do `ConfigPanel`. |
| D5 | Princípio do repo: nada em produção quebra ⇒ additive, sem stopgap | Endpoint aditivo; middleware/rotas existentes inalterados exceto o registro do novo módulo. |

---

## 1. Resumo executivo

Hoje só existe **reset de admin** (`POST /api/users/{user_id}/password`, gated por `users.manage` — [users.py:176](../server/routes/users.py#L176)) e a **senha única legada** do painel ([config.py:138](../server/routes/config.py#L138)). Não há endpoint de "trocar a MINHA senha". A solução é um endpoint self-service `POST /api/me/password` que: (1) identifica o usuário pela sessão (`current_user`), (2) verifica a senha atual com `verify_password_argon2`, (3) grava a nova com `hash_password_argon2` + `user_repo.set_password`; mais um modal "Trocar minha senha" na seção de conta do `GearMenu` (já visível a qualquer usuário logado). ⚠️ O endpoint **não pode** ficar sob `/api/auth/` (prefixo auth-exempt → `request.state.user` nunca é populado) — usa o precedente **não-isento** `/api/me/*` de [saved_filters.py](../server/routes/saved_filters.py).

---

## 2. Como funciona hoje (mapa)

| Peça | Onde | Comportamento atual |
|------|------|---------------------|
| Reset de senha (admin) | [users.py:176-189](../server/routes/users.py#L176) | `POST /api/users/{user_id}/password`, gated por `users.manage`. Define a senha de OUTRO usuário SEM pedir a atual. `hash_password_argon2` + `user_repo.set_password`. |
| Senha única legada | [config.py:138-147](../server/routes/config.py#L138), UI [ConfigPanel.js:249](../web/static/js/components/ConfigPanel.js#L249) | Campo "Senha do Painel" — SHA-256 compartilhado (`web_password_hash`), sem identidade de usuário. É o que o print do usuário mostra; **não** é a senha de conta. |
| Identidade da requisição | [authz.py:18-19](../server/authz.py#L18) | `current_user(request)` = `request.state.user` (dict do usuário RBAC ou `None`). |
| Middleware de auth | [app.py:486-541](../server/app.py#L486) | Popula `request.state.user` para paths `/api/` **não-isentos** quando há Bearer token (resolve via `resolve_request_token`). |
| ⚠️ Prefixos isentos | [app.py:469](../server/app.py#L469), checagem [app.py:510-512](../server/app.py#L510) | `/api/auth/` é **auth-exempt** → retorna antes de setar `request.state.user`. Um endpoint aqui NÃO enxerga o usuário logado. |
| Precedente `/api/me/*` | [saved_filters.py:34-92](../server/routes/saved_filters.py#L34), registro [app.py:695](../server/app.py#L695) | Rotas `/api/me/conversation-filters` (não-isentas) leem `current_user(request)`. **É o molde correto.** |
| Hash da senha (verificação) | [auth.py:33](../db/repositories/user_repo.py#L33) `get_auth_row(email)` → `{id, password_hash, is_active}` | `user_repo.get()` **remove** `password_hash` do dict ([user_repo.py:187](../db/repositories/user_repo.py#L187)), então `current_user` não tem o hash — é preciso re-buscar por `get_auth_row`. |
| Verificar / gravar | [auth.py:28-35](../server/auth.py#L28) `verify_password_argon2`; [auth.py:21-25](../server/auth.py#L21) `hash_password_argon2`; [user_repo.py:86-89](../db/repositories/user_repo.py#L86) `set_password` | Argon2id PHC. Mínimo de senha (8) usado em [users.py:88](../server/routes/users.py#L88) e [auth.py:147](../server/routes/auth.py#L147). |
| Seção de conta no menu | [GearMenu.js:174-189](../web/static/js/components/shell/GearMenu.js#L174) | Bloco no rodapé do menu da engrenagem que mostra `currentUser.name`/`email` + botão "Sair" **para qualquer usuário logado**. É onde entra o item "Trocar minha senha". |
| Host de modais | [App.js:11,16,75](../web/static/js/components/shell/App.js#L75) | `App` recebe `currentUser` e já hospeda `LowBalanceModal` + `PluginModalHost` — lugar natural do `ChangePasswordModal`. |
| Transporte + 401 global | [api.js:1-16](../web/static/js/services/api.js#L1) (facade sobre `httpClient.js`) | ⚠️ Há **um único branch 401 compartilhado** que dispara logout global. Erro de "senha atual errada" precisa ser **400**, não 401 (senão desloga o usuário). |

---

## 3. Inventário / análise

| # | Item | Arquivo (ponto de mudança) | O que falta | Abordagem | Risco | Esforço |
|---|------|----------------------------|-------------|-----------|-------|---------|
| I1 | Endpoint self-service | **novo** `server/routes/account.py` + import/registro em [app.py:21](../server/app.py#L21) e [app.py:695](../server/app.py#L695) | Não existe | `POST /api/me/password` lendo `current_user`; validações; 400 em erro de negócio | Médio | S |
| I2 | Buscar hash por id | [user_repo.py:33](../db/repositories/user_repo.py#L33) | `get_auth_row` só aceita email | Reusar `get_auth_row(current_user["email"])` **ou** adicionar `get_auth_row_by_id(user_id)` (mais robusto) | Baixo | S |
| I3 | Cliente API | [api.js:1090](../web/static/js/services/api.js#L1090) (perto do `resetUserPassword`) | Sem função self-service | `changeMyPassword(current, next)` → `request('POST','/api/me/password',…)` | Baixo | S |
| I4 | Modal de troca | **novo** `web/static/js/components/ChangePasswordModal.js` | Não existe | Form: senha atual + nova + confirmar; usa `wa-*`/`.wa-field`; erros inline | Baixo | M |
| I5 | Item no menu | [GearMenu.js:174-189](../web/static/js/components/shell/GearMenu.js#L174) | Bloco de conta só tem "Sair" | Item "Trocar minha senha" (só quando `currentUser`), chama `onChangePassword` | Baixo | S |
| I6 | Wiring do modal | [App.js:75](../web/static/js/components/shell/App.js#L75) | — | `useState` do modal; passar `onChangePassword` ao `GearMenu`; renderizar `ChangePasswordModal` | Baixo | S |
| I7 | Testes | [test_endpoints.py:1300+](../tests/test_endpoints.py#L1300) (seção "RBAC users + login") | Sem cobertura | Casos: sucesso, senha atual errada→400, curta→400, legado→recusa, login novo/antigo | Baixo | M |

### Falsos positivos descartados

| Suspeita | Por que NÃO é o caminho |
|----------|-------------------------|
| Reaproveitar `POST /api/users/{id}/password` passando o próprio id | Está gated por `users.manage` ([users.py:178](../server/routes/users.py#L178)) e **não pede a senha atual** (D2). Um atendente não tem `users.manage` — continuaria sem trocar. Fluxos distintos (D4). |
| Endpoint sob `/api/auth/change-password` | ⚠️ `/api/auth/` é **auth-exempt** ([app.py:469,510-512](../server/app.py#L510)) → o middleware retorna antes de setar `request.state.user` → `current_user()` = `None`. Impossível identificar quem troca. Tem que ser `/api/me/*`. |
| Criar permissão `profile.password` | D1: anti-padrão (usuário que não pode rotacionar a própria senha). Não mexer em `PERMISSION_CATALOG` ([permission_catalog.py:17](../domain/permission_catalog.py#L17)). |
| Retornar 401 em senha atual errada | ⚠️ Dispara o branch 401 global do `httpClient` → logout. Usar **400** com mensagem. |
| Nova rota SPA / aba no `GearMenu` | Excesso. Um **modal** disparado da seção de conta já visível ([GearMenu.js:174](../web/static/js/components/shell/GearMenu.js#L174)) é mais simples e não precisa de `screenRegistry`/roteamento. |
| Invalidar todas as sessões ao trocar | Tokens de sessão são opacos server-side (tabela `user_sessions`), **não** derivados da senha — trocar a senha não invalida sessões por si. Manter a sessão atual viva é o comportamento consistente com o reset de admin. (Ver P2 para invalidar as *outras* sessões, opcional.) |

---

## 4. Contrato do endpoint (fixo — frontend e backend podem ser feitos em paralelo contra ele)

```
POST /api/me/password
Headers: Authorization: Bearer <session_token do usuário RBAC>
Body:    { "current_password": "<atual>", "new_password": "<nova>" }

200 { "ok": true,  "data": { "updated": true } }
400 { "ok": false, "error": "A senha atual está incorreta." }
400 { "ok": false, "error": "A nova senha deve ter ao menos 8 caracteres." }
400 { "ok": false, "error": "A nova senha deve ser diferente da atual." }
403 { "ok": false, "error": "Disponível apenas para usuários autenticados." }  # msg atualizada no plano 48 (single-password aposentado)
```

Regras do handler (pseudo, sem implementar):
1. `user = current_user(request)`. Se `None` → **403** (D3: modo legado/aberto não tem identidade RBAC).
2. `new = body["new_password"]`; se `len(new) < 8` → 400. Se `new == current_password` → 400 (evita no-op).
3. `auth = user_repo.get_auth_row_by_id(user["id"])` (ou `get_auth_row(user["email"])`). Se `verify_password_argon2(current_password, auth["password_hash"])` for falso → **400** ("senha atual incorreta") — nunca 401.
4. `user_repo.set_password(user["id"], hash_password_argon2(new))` → 200.
5. Todas as chamadas de DB via `asyncio.to_thread` (padrão das rotas). Considerar rate-limit leve (ver P1).

---

## 5. Fases / Roadmap

```
WAVE 0  F1(backend) · F2(frontend)        ← contrato §4 fixo ⇒ os dois em paralelo
              │            │
              └─────┬──────┘  (barreira: F3 precisa de F1+F2)
WAVE 1        F3(testes + verificação manual)
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|------|------|-----------|-------|-------|---------------|
| 0 | F1 | Backend (endpoint + repo) | 🟢 [contrato §4] | Médio | `curl` do §4 responde 200/400/403 corretos |
| 0 | F2 | Frontend (api + modal + menu) | 🟢 [contrato §4; `[bloqueia: F3]`] | Baixo | Modal abre, valida e mostra erro/sucesso |
| 1 | F3 | Testes + verificação | 🔴 [depende de: F1, F2] | Baixo | Suíte verde no Postgres + fluxo manual ok |

> 🟢 = pode despachar junto (F1 e F2 não compartilham arquivos; o contrato §4 é a fronteira). 🔴 = sequencial (F3 só roda com F1+F2 prontos).

---

### Fase 1 — Backend: endpoint self-service 🟢
**Objetivo:** `POST /api/me/password` troca a senha do usuário logado, exigindo a atual.
**Itens:**
1. `[sequencial]` Criar `server/routes/account.py` com `register_routes(app, deps)` e a rota `POST /api/me/password` seguindo o contrato §4. Modelar o esqueleto em [saved_filters.py](../server/routes/saved_filters.py) (import `current_user`, `_ok`/`_err`, `asyncio.to_thread`).
2. `[paralelo]` Em [user_repo.py:33](../db/repositories/user_repo.py#L33), adicionar `get_auth_row_by_id(user_id)` (espelho de `get_auth_row`, filtrando por `users.c.id`) — evita round-trip por email e é robusto a e-mail nulo. (Alternativa aceitável: reusar `get_auth_row(user["email"])`.)
3. `[sequencial]` Registrar o módulo: import em [app.py:21](../server/app.py#L21) e `account_routes.register_routes(app, deps)` junto de [app.py:695](../server/app.py#L695) (perto de `saved_filters`).
4. `[sequencial]` Confirmar que o path `/api/me/password` **não** casa nenhum prefixo isento ([app.py:469,503-512](../server/app.py#L503)) — `/api/me/` é protegido, `request.state.user` é populado quando o Bearer token vem. ⚠️ NUNCA usar `/api/auth/...`.

**Pronto quando:**
- `curl -H "Authorization: Bearer <token de usuário>" -X POST /api/me/password -d '{"current_password":"errada","new_password":"outra12345"}'` → **400** "senha atual incorreta" (e o usuário **não** é deslogado).
- Com a senha atual correta → **200**; um novo `POST /api/auth/login` com a senha antiga → **401** e com a nova → **200**.
- Sem token (modo legado/aberto) → **403** com a mensagem de "apenas usuários".

#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-07-15)
- **O que foi feito:** novo `server/routes/account.py` (`POST /api/me/password`); `user_repo.get_auth_row_by_id` ([user_repo.py:46](../db/repositories/user_repo.py#L46)); registro em [app.py](../server/app.py) (import + `account_routes.register_routes`).
- **Como foi feito / decisões:** endpoint sob `/api/me/*` (não `/api/auth/*` isento); `current_user` None → 403; nova senha ≥8 e ≠ atual; verifica a atual com `verify_password_argon2` via `get_auth_row_by_id` → erro de negócio sempre 400 (nunca 401). Sem rate-limit dedicado (P1 adiado). Sessão atual não é invalidada (token opaco).
- **Problemas / pendências:** —
- **Verificação:** `py_compile` + import OK; coberto pela suíte (F3) — ver casos `/me/password` na seção RBAC.

---

### Fase 2 — Frontend: modal + item no menu 🟢
**Objetivo:** qualquer usuário logado abre "Trocar minha senha" e troca pelo modal.
**Itens:**
1. `[paralelo]` `api.js`: `changeMyPassword(currentPassword, newPassword)` → `request('POST','/api/me/password',{current_password,new_password})`, perto de [api.js:1090](../web/static/js/services/api.js#L1090).
2. `[paralelo]` Novo `web/static/js/components/ChangePasswordModal.js`: form com **senha atual**, **nova** e **confirmar nova**; validações client-side (≥8, nova==confirmar, nova≠atual); botão desabilitado até válido; erro do backend inline; toast/sucesso e fecha. Usar `wa-*` e `.wa-field` (modo escuro — ver [CLAUDE.md "Tema e modo escuro"]). Molde de modal: `ResetPasswordModal` em [UsersManager.js:199-219](../web/static/js/components/UsersManager.js#L199) (mas este exige **também** a senha atual e chama o novo endpoint).
3. `[sequencial]` `GearMenu.js`: no bloco de conta ([GearMenu.js:174-189](../web/static/js/components/shell/GearMenu.js#L174)), adicionar um item "Trocar minha senha" **somente quando `currentUser`** (não aparece em modo legado single-password — D3), chamando uma nova prop `onChangePassword` (+ `close()`).
4. `[sequencial]` `App.js`: `useState` `showChangePassword`; passar `onChangePassword=${() => setShowChangePassword(true)}` ao `<GearMenu>`; renderizar `<ChangePasswordModal>` ao lado de `LowBalanceModal` ([App.js:16](../web/static/js/components/shell/App.js#L16)).

**Pronto quando:** logado como usuário, o menu da engrenagem mostra "Trocar minha senha"; o modal valida (nova≠confirmar bloqueia; <8 bloqueia); senha atual errada mostra erro inline **sem deslogar**; sucesso fecha o modal. Em modo legado (só "Senha do Painel"), o item **não** aparece.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-07-15)
- **O que foi feito:** `api.js` `changeMyPassword`; novo `web/static/js/components/ChangePasswordModal.js`; item "Trocar minha senha" no bloco de conta do [GearMenu.js](../web/static/js/components/shell/GearMenu.js) (só com `currentUser`, prop `onChangePassword`); wiring em [App.js](../web/static/js/components/shell/App.js) (import + `showChangePassword` + render).
- **Como foi feito / decisões:** modal próprio (molde do `PasswordModal` de UsersManager) com senha atual + nova + confirmar; validações client-side (≥8, ≠atual, nova==confirmar); erro do backend inline; sucesso → `handleNotify` + fecha. Classes `wa-*`/`.wa-field` (modo escuro). Item some em modo legado (sem `currentUser`).
- **Problemas / pendências:** click-through no browser (abrir o modal, ver validações/erro inline no tema escuro) **não** executado aqui — recomendado o usuário validar no dev server (hot-reload; sem build step). Baixo risco: o modal clona o `PasswordModal` existente e o item segue o padrão exato dos botões do GearMenu.
- **Verificação:** `node --input-type=module --check` OK nos 4 arquivos; `handleNotify` confirmado em App.js:338; grafo de imports consistente (`changeMyPassword`/`ChangePasswordModal`/`onChangePassword` batem).

---

### Fase 3 — Testes + verificação manual 🔴 [depende de: F1, F2]
**Objetivo:** cobrir o endpoint na suíte e validar o fluxo ponta a ponta.
**Itens:**
1. `[sequencial]` Em [test_endpoints.py](../tests/test_endpoints.py), na seção "RBAC users + login" (~[linha 1300](../tests/test_endpoints.py#L1300)), após o login do usuário: `POST /api/me/password` com senha atual errada → **400**; senha nova curta (<8) → **400**; nova válida → **200**; então `login` com a antiga → **401** e com a nova → **200**. Um caso sem token/legado → **403**. (Padrão do helper `check(...)`.)
2. `[sequencial]` Rodar a suíte no Postgres de teste (`WHATSBOT_TEST_DB_URL`, banco com `test` no nome).
3. `[sequencial]` Verificação manual: subir `./linux_start.sh`, logar como um atendente (ex.: "Atendente"), trocar a senha pelo modal, deslogar e relogar com a nova.

**Pronto quando:** `venv/bin/python -m pytest tests/test_endpoints.py -q` **verde**; o fluxo manual funciona; nenhum segredo aparece na URL.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída (2026-07-15)
- **O que foi feito:** casos de `/api/me/password` na seção RBAC de [test_endpoints.py](../tests/test_endpoints.py) (wrong current→400, short→400, same→400, valid→200, login antigo→401/novo→200, sem sessão→401/403, sessão sobrevive à troca, restore).
- **Como foi feito / decisões:** usa a sessão `_utok` do admin; restaura a senha ao fim para não perturbar downstream. O caso "sem sessão" usa `in (401, 403)` porque o F0 do plano 48 troca 403→401 (middleware bloqueia antes do handler).
- **Problemas / pendências:** —
- **Verificação:** `venv/bin/python tests/test_endpoints.py` → **1240 passed, 0 failed** no Postgres de teste.

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Path do endpoint | Usar `/api/auth/...` (isento) ⇒ `current_user` = `None`, endpoint inútil | Usar `/api/me/password` (não-isento); teste explícito de que o usuário é identificado. |
| Código HTTP de erro | 401 em senha atual errada dispara logout global (branch único do `httpClient`) | Retornar **400** para todos os erros de negócio; reservar 401 para "sem sessão". |
| Vazamento de hash | Expor `password_hash` ao verificar | `current_user`/`get()` já removem o hash ([user_repo.py:187](../db/repositories/user_repo.py#L187)); usar `get_auth_row*` só no servidor, nunca retornar no JSON. |
| Modo legado | Usuário single-password sem identidade tenta trocar | 403 com mensagem clara; item some do menu (só com `currentUser`). |
| Modo escuro | Modal novo ilegível no tema escuro | `wa-*` + `.wa-field`; testar com `.dark` ligado (regra do CLAUDE.md). |
| Força bruta na senha atual | Endpoint vira oráculo de senha | Ver P1 (rate-limit leve por usuário/IP, reusando o molde de [auth.py:20-48](../server/routes/auth.py#L20)). |
| Sessões antigas | Após trocar, sessões em outros dispositivos continuam válidas | Comportamento aceito (tokens opacos, não derivados da senha); P2 decide se invalida as *outras*. |
| Postgres | Suíte precisa do banco de teste | `WHATSBOT_TEST_DB_URL` com `test` no nome (trava de segurança). |

---

## 7. Perguntas em aberto

- **P1 — Rate-limit no endpoint?** ⏸️ ADIADO (default: sem limite dedicado no MVP). Contexto: a senha atual é verificada a cada POST; sem limite, é um oráculo. Opções: (a) reusar o limitador por-IP de [auth.py:20-48](../server/routes/auth.py#L20); (b) limite por-usuário (ex.: 10/15min). **Recomendação:** (a) leve, se sobrar esforço na F1; não bloqueia o MVP.
- **P2 — Invalidar as OUTRAS sessões ao trocar?** ⏸️ ADIADO (default: NÃO). Contexto: por segurança, trocar a senha poderia derrubar sessões antigas (deletar `user_sessions` do usuário exceto a atual). Opções: (a) manter todas (mais simples, consistente com o reset de admin); (b) `session_repo.delete_others(user_id, keep_token)`. **Recomendação:** (a) no MVP; (b) como melhoria futura.
- **P3 — Onde mora o endpoint?** ✅ DECIDIDO (2026-07-15): módulo novo `server/routes/account.py`. Alternativa considerada e descartada: enfiar em `auth.py` (confunde, pois lá é o namespace isento) ou em `saved_filters.py` (semântica de "filtros", não de conta).

---

## 8. Apêndice — arquivos-chave

**Backend**
- `server/routes/account.py` — **novo**: `POST /api/me/password`.
- [server/app.py:21](../server/app.py#L21) e [:695](../server/app.py#L695) — import + `register_routes`.
- [db/repositories/user_repo.py:33](../db/repositories/user_repo.py#L33) — `get_auth_row_by_id` (novo, ou reuso de `get_auth_row`); [:86](../db/repositories/user_repo.py#L86) `set_password` (reuso).
- [server/auth.py:21](../server/auth.py#L21) / [:28](../server/auth.py#L28) — `hash_password_argon2` / `verify_password_argon2` (reuso).
- [server/authz.py:18](../server/authz.py#L18) — `current_user` (reuso).

**Frontend**
- `web/static/js/components/ChangePasswordModal.js` — **novo**.
- [web/static/js/services/api.js:1090](../web/static/js/services/api.js#L1090) — `changeMyPassword`.
- [web/static/js/components/shell/GearMenu.js:174](../web/static/js/components/shell/GearMenu.js#L174) — item no bloco de conta.
- [web/static/js/components/shell/App.js:75](../web/static/js/components/shell/App.js#L75) — estado + wiring do modal.

**Testes**
- [tests/test_endpoints.py:1300](../tests/test_endpoints.py#L1300) — seção RBAC.

---

## 9. Checklist de verificação

- [ ] `POST /api/me/password` com senha atual **correta** → 200 e a nova senha loga; a antiga não.
- [ ] Senha atual **errada** → **400** (mensagem clara) e o usuário **não** é deslogado (não 401).
- [ ] Nova senha `< 8` ou igual à atual → 400.
- [ ] Sem sessão RBAC (modo legado/aberto) → **403**; item "Trocar minha senha" **ausente** do menu.
- [ ] O item aparece para um atendente **sem** `users.manage` (não é gated por permissão — D1).
- [ ] `password_hash` nunca aparece em nenhuma resposta JSON.
- [ ] Modal legível no **modo escuro** (`wa-*`/`.wa-field`).
- [ ] `venv/bin/python -m pytest tests/test_endpoints.py -q` **verde** no Postgres (`WHATSBOT_TEST_DB_URL`).
- [ ] Nenhum segredo (senha) na URL — tudo no corpo do POST.
- [ ] Reload / voltar-avançar do navegador não quebra o fluxo do modal.
