# Plano 86 — IP público do navegador na auditoria (o painel informa, o core registra)

> **Status:** EXECUTADO (2026-07-29) — Fases 0/A1/A2/A3/B1/B2/C1/E1/E2 concluídas; **V1 parcial** (itens de navegador/4G dependem do usuário). Commit pendente de autorização · **Data:** 2026-07-29 · **Escopo:** pequeno/médio
> **Origem:** pedido do usuário — a coluna IP da tela `/audit` mostra sempre `10.8.200.4` (o proxy), e o objetivo é distinguir quem agiu de dentro do escritório de quem agiu de fora. **Método:** leitura do código real (`arquivo:linha`) + **medição de rede com `tcpdump` na porta 8090** (LAN, internet pública e tráfego real do painel aberto) + consulta ao banco de produção (`select ip_address, count(*) from audit_log group by 1`).
> O IP de origem é destruído num hop **antes** do Traefik, então `X-Forwarded-For` chega sempre como `10.8.200.4` e **nenhum código do backend consegue recuperá-lo**. A solução aceita pelo usuário é inverter o sentido: o **painel** descobre o próprio IP público (Cloudflare `/cdn-cgi/trace`), manda num cabeçalho em toda chamada à API, e o core prefere esse valor ao resolver o ator da auditoria. Valor autodeclarado — logo, forjável — e isso está **aceito e travado** (D1).
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | ✅ (2026-07-29) O valor é **autodeclarado pelo cliente e portanto forjável**, e isso está aceito — "a maioria dos usuários é leiga e não pensará em fraudar" | Nada de assinatura HMAC, nada de endpoint de eco próprio, nada de rota de rede alternativa. O backend confia no cabeçalho para a **auditoria** |
| D2 | ✅ (2026-07-29) Só **IP público**. O IP privado (`10.10.100.10`) foi descartado: navegadores modernos ocultam o endereço local por design (mDNS `*.local` no WebRTC desde ~2020) — não existe API que o devolva | O cabeçalho carrega **um** valor: o IP público de saída do navegador |
| D3 | ✅ (2026-07-29) Grava no **mesmo campo `ip_address`** da `audit_log` | Tela `/audit` ([AuditLog.js:127](../web/static/js/components/AuditLog.js#L127)), filtros e export CSV/JSON funcionam **sem alteração**. Sem migration |
| D4 | ✅ (2026-07-29) O cabeçalho alimenta **apenas a auditoria**, NUNCA o rate-limit de login | Se alimentasse o bucket de `/api/auth/login` ([auth.py:35](../server/routes/auth.py#L35)), bastaria variar o cabeçalho a cada tentativa para anular o limite de 10 falhas/15 min. O rate-limit continua no IP **observado na rede**. É separação de mecanismo, não reabertura de D1 |
| D5 | ✅ (2026-07-29) Fonte = `https://www.cloudflare.com/cdn-cgi/trace` (texto simples, sem chave, sem cadastro). Falha ⇒ **degradação silenciosa** para o IP de rede atual | Uma entrada específica na CSP (host exato, nunca curinga). Nenhuma ação de usuário pode quebrar por causa disso |
| D6 | ✅ (2026-07-29) **Não implementar nada externo agora** (endpoint de eco assinado ficou fora) | Registrado em §6/P4 como caminho de evolução se um dia o valor precisar valer como prova |

---

## 1. Resumo executivo

Toda linha da `audit_log` grava `10.8.200.4` porque o IP real do cliente é apagado antes de chegar ao app — **medido**, não inferido: requisições da LAN, da internet pública e do próprio painel aberto chegam todas com `X-Forwarded-For: 10.8.200.4`. Como o dado nunca entra no processo, nenhuma mudança de resolução no backend o recupera.

A solução tem três peças pequenas: **(A)** um módulo de frontend que busca o IP público **uma vez por carregamento** e o guarda em memória; **(B)** injeção desse valor num cabeçalho no **único seam** por onde passam todas as chamadas do painel — `authHeaders()` ([httpClient.js:32](../web/static/js/services/httpClient.js#L32)), usado por 47 call sites, **incluindo o transporte de plugin** ([plugins/api.js:129](../web/static/js/plugins/api.js#L129)); **(C)** no core, uma função nova que prefere o cabeçalho ao resolver o ator da auditoria, mantendo o rate-limit no IP de rede (D4).

A base do backend **já está escrita e sem commit** (Fase 0): [server/client_ip.py](../server/client_ip.py) resolve a cadeia `X-Forwarded-For` da direita para a esquerda (não-forjável) e já é consumida pelo ator da auditoria e pelo rate-limit.

---

## 2. Como funciona hoje (mapa)

### 2.1 Evidência de rede (medida em 2026-07-29)

| Origem da requisição | Peer TCP visto pelo app | `X-Forwarded-For` que chegou |
|---|---|---|
| LAN — `10.8.200.101` | `10.8.200.211` | `10.8.200.4` |
| Internet pública (infra Anthropic) | `10.8.200.211` | `10.8.200.4` |
| Painel real aberto no navegador da operadora (3 requisições) | `10.8.200.211` | `10.8.200.4` |

Cabeçalhos auxiliares presentes em todas: `X-Real-Ip: 10.8.200.4` e `X-Forwarded-Server: 896c39ec71f5` (container do Traefik/Coolify). ⚠️ O Traefik está **correto** — ele anota o peer que enxerga; quem enxerga já é `10.8.200.4`. A perda é a montante dele (SNAT / proxy L7 sem repasse de XFF).

Distribuição no banco: `10.8.200.4` → 84 linhas · `10.8.8.133` → 22 · `127.0.0.1` → 1.

### 2.2 Caminho do IP dentro do core

| Passo | Local | Comportamento |
|-------|-------|---------------|
| Resolução | [server/client_ip.py:128](../server/client_ip.py#L128) `client_ip(request)` | Monta a cadeia XFF + peer, caminha da **direita para a esquerda** pulando hops confiáveis, devolve o primeiro não-confiável. Todo hop confiável ⇒ o mais à esquerda |
| Carimbo do ator | [server/app.py:603](../server/app.py#L603) | `_ip = client_ip(request)` → `ActorCtx(ip=_ip)` no `ContextVar` |
| Escrita | [server/audit_listener.py:81](../server/audit_listener.py#L81) e [:124](../server/audit_listener.py#L124) | `audit_repo.add(..., ip_address=actor.ip)` ([audit_repo.py:49](../db/repositories/audit_repo.py#L49)) |
| Plugins | [plugins/context.py](../plugins/context.py) `audit()` | **Herdam** o `ActorCtx` — nenhuma alteração de plugin é necessária, nem agora nem depois |
| Rate-limit de login | [server/routes/auth.py:23-27](../server/routes/auth.py#L23) | Mesmo helper hoje; passa a divergir por D4 |
| Exibição | [AuditLog.js:127](../web/static/js/components/AuditLog.js#L127) | `${row.ip_address || '—'}` — nada a mudar (D3) |

### 2.3 O seam do frontend (o achado que dimensiona a Fase A2)

⚠️ Injetar o cabeçalho em `httpClient.request()` **seria insuficiente**: ~20 call sites chamam `fetch()` direto (PluginsManager, SoundSettings, ToolsUnified, useSoundPrefs, soundEngine, App.js e 5 pontos do próprio `api.js` — QR, export de contatos, QR de canal, export de auditoria, `auth/check`).

Mas **todos** eles montam os cabeçalhos com `authHeaders()`:

```js
// httpClient.js:32 — o ponto único
export function authHeaders(headers = {}) {
  const token = getToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return headers;
}
```

47 usos no `web/static/js`, incluindo `plugins/api.js:129` (transporte usado pelas telas de plugin). Uma linha aqui cobre core + plugins sem monkey-patch de `window.fetch`.

---

## 3. Inventário / análise

| # | Item | Onde | O que falta | Abordagem | Risco | Esforço |
|---|------|------|-------------|-----------|-------|---------|
| 1 | Descobrir o IP público no cliente | novo `web/static/js/services/publicIp.js` | não existe | Fetch único com `AbortController` (timeout ~3s), parser puro do corpo `key=value`, cache em memória | baixo | S |
| 2 | Enviar o valor em toda chamada | [httpClient.js:32](../web/static/js/services/httpClient.js#L32) | não existe | 2 linhas dentro de `authHeaders()` | baixo | S |
| 3 | Disparar a busca cedo no boot | [app.js:18](../web/static/js/app.js#L18) / [AuthGate.js:28](../web/static/js/components/shell/AuthGate.js#L28) | não existe | Chamada fire-and-forget antes do `render()` | baixo | S |
| 4 | Aceitar e validar o cabeçalho | [server/client_ip.py:128](../server/client_ip.py#L128) | não existe | `reported_public_ip()` (exige `is_global`) + `audit_ip()` | baixo | S |
| 5 | Usar só na auditoria (D4) | [server/app.py:603](../server/app.py#L603) | hoje usa `client_ip` | Trocar para `audit_ip`; `auth.py` **não muda** | baixo | S |
| 6 | Liberar o host na CSP | [server/app.py:651](../server/app.py#L651) | `connect-src 'self' ws: wss:` | Acrescentar o host exato | médio¹ | S |
| 7 | Cobertura de teste | `tests/test_client_ip.py`, `tests/test_endpoints.py`, novo `publicIp.test.js` | parcial | Unitário nos dois lados + ponta-a-ponta cabeçalho → `audit_log` | baixo | M |
| 8 | Documentar | [CLAUDE.md:837](../CLAUDE.md#L837) | bullet do plano anterior | Estender o bullet "IP do cliente atrás de proxy reverso" | baixo | S |

¹ Risco médio **não** pelo host em si (é um domínio fixo e conhecido), e sim porque a CSP é um controle de segurança global: a alteração precisa ser cirúrgica (host exato, uma diretiva) e conferida no console do navegador.

### Falsos positivos descartados

| Hipótese | Por que NÃO é o caminho |
|---|---|
| "Configurar `WHATSBOT_TRUSTED_PROXY_HOPS`" | Não resolve: o IP real **não está em posição nenhuma** da cadeia. Medido — a cadeia inteira é `10.8.200.4` + peer |
| "Ligar `proxy_headers`/`forwarded_allow_ips` no uvicorn" ([main.py:85](../main.py#L85)) | Irrelevante: o core lê o cabeçalho diretamente, e o cabeçalho já chega envenenado |
| "Traefik mal configurado" | O Traefik faz o correto (anota o peer que vê, e assina com `X-Forwarded-Server`). A perda é a montante dele |
| "Pegar o IP privado via WebRTC" | Fechado por design nos navegadores desde ~2020 (candidatos locais viram `<uuid>.local`). Só funcionaria com permissão de câmera/microfone concedida — inaceitável |
| "Mandar o IP pelo WebSocket" | O `WebSocket` do navegador não permite cabeçalhos customizados, e **nenhuma linha de auditoria nasce do WS** |
| "Injetar em `httpClient.request()`" | Insuficiente — ~20 `fetch()` diretos ficariam de fora. O seam certo é `authHeaders()` (§2.3) |
| "Passar a gravar em `sessions.ip` no login também" | O login acontece **antes** de o valor existir. Ver P3 |

---

## 4. Fases / Roadmap

```
WAVE 0   A1 · B1 · C1                       ← independentes, despachar juntas
            │    │
            │    └── B1 habilita B2
            └────── A1 habilita A2 e A3
WAVE 1   A2 · A3 · B2                       ← paralelas entre si, dependem da Wave 0
            └──────────┬─────────┘
WAVE 2   E1 → E2                            ← E1 precisa das duas pontas ligadas
WAVE 3   V1                                 ← validação na instância + commit
```

| Wave | Fase | Workstream | Paralelismo | Risco | Pronto quando |
|------|------|-----------|-------------|-------|---------------|
| — | **0** | base backend (já feita) | ✅ concluída | baixo | Já implementada, aguardando commit |
| 0 | **A1** | frontend | 🟢 | baixo | `publicIp.js` + teste puro verde |
| 0 | **B1** | backend | 🟢 | baixo | `reported_public_ip`/`audit_ip` + testes verdes |
| 0 | **C1** | backend/CSP | 🟢 | médio | Console do navegador sem violação de CSP |
| 1 | **A2** | frontend | 🟢 `[depende de: A1]` | baixo | Cabeçalho visível na aba Network |
| 1 | **A3** | frontend | 🟢 `[depende de: A1]` | baixo | Busca dispara antes do primeiro render |
| 1 | **B2** | backend | 🟢 `[depende de: B1]` `[bloqueia: E1]` | baixo | Auditoria usa o cabeçalho; rate-limit não |
| 2 | **E1** | testes | 🔴 `[depende de: A2, B2]` | baixo | `tests/test_endpoints.py` verde com o caso novo |
| 2 | **E2** | docs | 🟢 | baixo | `CLAUDE.md` atualizado |
| 3 | **V1** | validação | 🔴 `[depende de: tudo]` | médio | IP público real aparecendo em `/audit` |

Disciplina do repo aplicável: **verde a cada fase**; **um refactor por commit**; nunca avançar com teste vermelho não explicado.

---

### Fase 0 — Base de resolução de IP (JÁ IMPLEMENTADA, sem commit)

**Objetivo:** ter um ponto único, correto e não-forjável de resolução de IP antes de acrescentar o caminho autodeclarado.

**Itens** (todos concluídos em 2026-07-29):
1. [server/client_ip.py](../server/client_ip.py) — novo. `client_ip()` caminha a cadeia da direita para a esquerda pulando hops confiáveis; envs `WHATSBOT_TRUSTED_PROXIES` (lista de CIDRs) e `WHATSBOT_TRUSTED_PROXY_HOPS` (contagem exata); `normalize_ip()` tolera `1.2.3.4:5678` e `[::1]:443`.
2. [server/app.py:603](../server/app.py#L603) — o ator da auditoria passou a usar o helper (era `xff.split(",")[0]`, **forjável**).
3. [server/routes/auth.py:23-27](../server/routes/auth.py#L23) — o bucket de rate-limit também (era o mesmo `split[0]`: dava para anular o limite variando o cabeçalho).
4. [tests/test_client_ip.py](../tests/test_client_ip.py) — 16 casos, incluindo `test_cenario_atual_da_instancia_nao_regride` (a cadeia de hoje continua gravando `10.8.200.4`).
5. [CLAUDE.md:837](../CLAUDE.md#L837) — bullet "IP do cliente atrás de proxy reverso" nos Gotchas.

**Pronto quando:** ✅ `tests/test_client_ip.py` 16/16 · `tests/test_audit.py` 28/28 · `tests/test_endpoints.py` 1626/1626.

#### Status de execução — Fase 0
**Estado:** ✅ Concluída (código escrito e verde) · ⏸️ commit pendente de autorização do usuário
- **O que foi feito:** `server/client_ip.py` (novo), `server/app.py` (import + linha 603), `server/routes/auth.py` (import + `_client_ip`), `tests/test_client_ip.py` (novo), `CLAUDE.md` (1 bullet).
- **Como foi feito / decisões:** direita→esquerda com conjunto de confiança configurável, em vez do `split(",")[0]` forjável; alinhado à convenção que os plugins `protocolos`/`website` já usavam. Fallback para o mais à esquerda quando todo hop é confiável — preserva byte a byte o valor gravado hoje.
- **Problemas / pendências:** nada técnico. **Falta o commit** (o usuário ainda não autorizou).
- **Verificação:** 16/16 + 28/28 + 1626/1626 verdes.

---

### Fase A1 — Módulo `publicIp.js` (busca + cache + parser puro) 🟢

**Objetivo:** descobrir o IP público do navegador uma vez por carregamento, sem nunca quebrar nada se falhar.

**Itens:**
1. `[paralelo]` Criar `web/static/js/services/publicIp.js` com:
   - `parseTrace(text)` — **puro**, extrai `ip=` do corpo `key=value` por linha do `/cdn-cgi/trace`; devolve `null` para corpo inesperado.
   - `initPublicIp()` — dispara o `fetch` com `AbortController` (timeout ~3s), `cache: 'no-store'`, `credentials: 'omit'`; guarda em módulo; **idempotente** (segunda chamada não refaz).
   - `getPublicIp()` — devolve o valor em memória ou `''`.
   - Todo o caminho de erro engolido (`.catch(() => {})`): rede bloqueada, CSP, offline e timeout **não** podem gerar exceção não tratada nem log ruidoso.
2. `[paralelo]` Criar `web/static/js/services/publicIp.test.js` (`node --test`) cobrindo `parseTrace`: corpo real do Cloudflare, IPv6, corpo vazio, corpo sem a chave `ip`, lixo.

⚠️ Não usar `localStorage`/`sessionStorage` nesta fase (ver P1) — memória por carregamento de página.

**Pronto quando:** `node --test web/static/js/services/publicIp.test.js` verde; o módulo não importa nada de `preact` (é puro/serviço).

#### Status de execução — Fase A1
**Estado:** ✅ Concluída (2026-07-29)
- **O que foi feito:** `web/static/js/services/publicIp.js` (novo — `parseTrace`, `initPublicIp`, `getPublicIp`) e `web/static/js/services/publicIp.test.js` (novo, 8 casos).
- **Como foi feito / decisões:** `parseTrace` percorre linha a linha e compara a chave **trimada** exatamente com `ip` — não usa `startsWith('ip=')`, senão `sip=`/`ipx=` casariam (há teste). Valor vazio ⇒ `null`. `initPublicIp` guarda a promise em voo (`inflight`) além do valor, então duas chamadas concorrentes no boot não geram duas consultas. Todo o caminho de erro é engolido num `try/catch` único (offline, CSP, abort do timeout) e `clearTimeout` roda em `finally`. Só memória, sem `sessionStorage` (P1 segue adiada).
- **Problemas / pendências:** nenhuma. O único ajuste foi num teste meu que assumia que `'  ip = x  '` não casaria — o parser é (corretamente) tolerante a espaço em volta da chave; a asserção foi corrigida para o comportamento real.
- **Verificação:** `node --test web/static/js/services/publicIp.test.js` → 8/8 verdes.

---

### Fase B1 — `reported_public_ip()` + `audit_ip()` no core 🟢

**Objetivo:** aceitar o cabeçalho **validando o que entra**, sem misturar com a resolução de rede.

**Itens:**
1. `[paralelo]` Em [server/client_ip.py](../server/client_ip.py), acrescentar (sem tocar em `client_ip()`, que continua sendo a verdade de rede):

   ```python
   REPORTED_IP_HEADER = "x-client-public-ip"

   def reported_public_ip(request) -> str | None:  # None se ausente/inválido/não-global
   def audit_ip(request) -> str | None:            # reported_public_ip(...) or client_ip(...)
   ```
2. `[paralelo]` Validação em `reported_public_ip`: passar por `normalize_ip()` ([client_ip.py:85](../server/client_ip.py#L85)) e **exigir `ipaddress.ip_address(x).is_global`** — recusa privado, loopback, link-local, CGNAT e lixo. Assim um cliente que mande besteira não polui a coluna; cai no valor de rede.
3. `[paralelo]` Docstring registrando explicitamente D1 (valor autodeclarado, aceito) e D4 (não serve a rate-limit).
4. `[paralelo]` Estender `tests/test_client_ip.py`: cabeçalho válido vence a rede; cabeçalho privado é ignorado; cabeçalho com lixo é ignorado; ausente ⇒ comportamento idêntico ao de hoje; `client_ip()` **inalterado** em todos os casos.

**Pronto quando:** `venv/bin/python -m pytest tests/test_client_ip.py -q` verde, com os 16 casos anteriores intactos.

#### Status de execução — Fase B1
**Estado:** ✅ Concluída (2026-07-29)
- **O que foi feito:** `server/client_ip.py` ganhou `REPORTED_IP_HEADER`, `reported_public_ip()` e `audit_ip()` (bloco no fim do arquivo, `client_ip()` intacto) + um parágrafo no docstring do módulo dizendo que as duas funções são separadas de propósito. `tests/test_client_ip.py` ganhou 6 casos.
- **Como foi feito / decisões:** validação = `normalize_ip()` (já tolera porta e `[::1]`) + `ipaddress.ip_address(x).is_global`; `try/except ValueError` mantido como cinto de segurança mesmo com o `normalize_ip` antes. `audit_ip` é `reported or client_ip` — uma linha, sem estado. As docstrings registram D1 (autodeclarado/forjável, aceito) e D4 (nunca em rate-limit) por escrito.
- **Problemas / pendências:** nada. Atenção registrada para quem escrever testes: `2001:db8::/32` é faixa de **documentação** e reprova em `is_global` — o teste de IPv6 usa `2606:4700:4700::1111`.
- **Verificação:** `venv/bin/python -m pytest tests/test_client_ip.py -q` → **22/22** (16 antigos intactos + 6 novos: cabeçalho vence a rede; ausente = idêntico a hoje; privado ignorado (8 faixas); lixo ignorado (7 formas); IPv6/porta; sem rede nem cabeçalho ⇒ `None`).

---

### Fase C1 — CSP libera o host da consulta 🟢

**Objetivo:** permitir a única chamada externa do painel, sem afrouxar mais nada.

**Itens:**
1. `[sequencial]` Em [server/app.py:651](../server/app.py#L651), mudar **só** a diretiva `connect-src`:
   `"connect-src 'self' ws: wss: https://www.cloudflare.com; "`.
2. `[sequencial]` Conferir que nenhuma outra diretiva foi tocada (`default-src`, `script-src`, `img-src`, `frame-ancestors 'none'` seguem idênticas). Host exato — **nunca** curinga, nunca `*.cloudflare.com`.
3. `[sequencial]` Registrar em comentário por que o host está ali (senão vira mistério na próxima auditoria de segurança).

⚠️ A rota do widget embutível define a **própria** CSP e faz opt-out do bloqueio de frame ([app.py:636-642](../server/app.py#L636)) — confirmar que ela não herda nem precisa desta entrada.

**Pronto quando:** painel recarregado com o console limpo (sem `Refused to connect ... violates Content Security Policy`) e a requisição ao `/cdn-cgi/trace` com status 200 na aba Network.

#### Status de execução — Fase C1
**Estado:** ✅ Concluída (2026-07-29) · validação no navegador pendente (V1)
- **O que foi feito:** `server/app.py` — só a diretiva `connect-src` mudou, para `'self' ws: wss: https://www.cloudflare.com`, com comentário de 4 linhas explicando por que o host está ali.
- **Como foi feito / decisões:** host exato, sem curinga. Nenhuma outra diretiva tocada (`default-src`, `script-src`, `worker-src`, `style-src`, `img-src`, `media-src`, `frame-ancestors 'none'` idênticas — conferido no diff).
- **Problemas / pendências:** ⚠️ conferido o ponto de atenção do plano: a rota do widget (`assets/plugin_examples/website/routes.py:174,179`) devolve a **própria** CSP, que contém APENAS `frame-ancestors` — não herda a do core e não precisa desta entrada (o middleware respeita a CSP da rota).
- **Verificação:** diff conferido; console do navegador fica para a Fase V1 (instância).

---

### Fase A2 — Injetar o cabeçalho no seam único 🟢 `[depende de: A1]`

**Objetivo:** todas as chamadas do painel (core **e** plugins) passarem a carregar o valor, sem tocar em ~20 call sites.

**Itens:**
1. `[sequencial]` Em [httpClient.js:32](../web/static/js/services/httpClient.js#L32) `authHeaders()`, acrescentar o valor quando existir:
   ```js
   const ip = getPublicIp();
   if (ip) headers['X-Client-Public-IP'] = ip;
   ```
2. `[sequencial]` Atualizar o JSDoc da função (ela deixa de ser "só auth") e o cabeçalho do módulo, que hoje descreve o arquivo como consolidação do fetch+401.
3. `[sequencial]` Conferir que `plugins/api.js:129` continua herdando (importa `authHeaders` de `httpClient.js` — herda por construção, mas vale a checagem no diff).

⚠️ Nada de monkey-patch em `window.fetch`: `authHeaders()` já é o funil real (47 usos) e a mudança fica rastreável.
⚠️ Cabeçalho **ausente** quando o valor ainda não chegou — nunca mandar string vazia (o backend trataria como lixo e cairia no fallback de qualquer forma, mas o diff fica mais honesto assim).

**Pronto quando:** na aba Network, qualquer ação do painel (ex.: salvar uma configuração) mostra `X-Client-Public-IP` no request; uma tela de plugin também.

#### Status de execução — Fase A2
**Estado:** ✅ Concluída (2026-07-29) · conferência na aba Network pendente (V1)
- **O que foi feito:** `web/static/js/services/httpClient.js` — `import { getPublicIp }`, duas linhas em `authHeaders()`, JSDoc da função reescrito (deixou de ser "só auth") e nota no cabeçalho do módulo.
- **Como foi feito / decisões:** cabeçalho **omitido** enquanto o valor não chega (nunca string vazia). Sem monkey-patch de `window.fetch`.
- **Problemas / pendências:** conferido que `web/static/js/plugins/api.js` monta os cabeçalhos com `authHeaders()` (linha do `req()` do `buildPluginHttp`) — herda por construção, sem alteração.
- **Verificação:** diff; validação visual na aba Network fica para V1.

---

### Fase A3 — Disparar a busca o mais cedo possível 🟢 `[depende de: A1]`

**Objetivo:** encurtar a janela em que as primeiras ações do usuário ainda gravam o IP de rede.

**Itens:**
1. `[sequencial]` Chamar `initPublicIp()` em [app.js](../web/static/js/app.js#L18) **antes** do `render(html\`<${AuthGate} />\`, ...)` — o módulo de entrada é minúsculo (18 linhas) e roda antes de qualquer tela.
2. `[sequencial]` Não bloquear o render: a chamada é fire-and-forget, o painel monta na mesma velocidade de hoje.
3. `[sequencial]` Verificar o efeito prático: o login em si (`POST /api/auth/login`) provavelmente sai **sem** o cabeçalho, e isso é esperado — ver Riscos.

**Pronto quando:** com o painel recarregado, a requisição ao `/cdn-cgi/trace` aparece entre as primeiras da aba Network, e a segunda ação do usuário já carrega o cabeçalho.

#### Status de execução — Fase A3
**Estado:** ✅ Concluída (2026-07-29) · conferência no navegador pendente (V1)
- **O que foi feito:** `web/static/js/app.js` — `import { initPublicIp }` + a chamada fire-and-forget imediatamente ANTES do `render()`, com comentário de 3 linhas.
- **Como foi feito / decisões:** sem `await` (o boot não espera) e sem `.catch()` no call site — o módulo já engole tudo internamente, então não há promise rejeitada solta.
- **Problemas / pendências:** o `POST /api/auth/login` deve mesmo sair **sem** o cabeçalho na maioria dos casos (a consulta externa é mais lenta que o clique? não — mais lenta que o render, e o login é a primeira ação). Comportamento esperado e documentado (E2); P1 (`sessionStorage`) é a saída se isso incomodar.
- **Verificação:** diff; ordem das requisições na aba Network fica para V1.

---

### Fase B2 — A auditoria passa a preferir o cabeçalho 🟢 `[depende de: B1]` `[bloqueia: E1]`

**Objetivo:** ligar a ponta do backend — **só** na auditoria (D4).

**Itens:**
1. `[sequencial]` Em [server/app.py:603](../server/app.py#L603), trocar `client_ip(request)` por `audit_ip(request)` e ajustar o import ([app.py:20](../server/app.py#L20)).
2. `[sequencial]` **Não tocar** em [server/routes/auth.py](../server/routes/auth.py#L23) — o rate-limit permanece no IP de rede (D4). Deixar isso explícito num comentário curto nos dois arquivos, senão um refactor futuro "unifica" os dois e reabre o furo.
3. `[sequencial]` Confirmar que nada mais consome `ActorCtx.ip` além de [audit_listener.py:81](../server/audit_listener.py#L81)/[:124](../server/audit_listener.py#L124) e [routes/audit.py:93](../server/routes/audit.py#L93) (`grep -rn "actor.ip\|actor_ctx.ip"`).

**Pronto quando:** `tests/test_client_ip.py` e `venv/bin/python tests/test_audit.py` verdes; `tests/test_endpoints.py` sem regressão.

#### Status de execução — Fase B2
**Estado:** ✅ Concluída (2026-07-29)
- **O que foi feito:** `server/app.py` — import `client_ip` → `audit_ip` e a linha do ator (`_ip = audit_ip(request)`), com comentário explicando D4. `server/routes/auth.py` — **nenhuma mudança de comportamento**, só o comentário-trava no `_client_ip` ("`client_ip`, NUNCA `audit_ip`").
- **Como foi feito / decisões:** o import passou a trazer só `audit_ip` (o `app.py` não usa mais `client_ip` diretamente) — a separação fica visível já no topo do arquivo.
- **Problemas / pendências:** `grep` confirmou que os ÚNICOS consumidores de `ActorCtx.ip` são `server/audit_listener.py:81`, `:124` e `server/routes/audit.py:93` — exatamente os previstos no plano; nenhum outro caminho passou a receber o valor autodeclarado.
- **Verificação:** `tests/test_client_ip.py` 22/22 · `venv/bin/python tests/test_audit.py` **28/28** · `tests/test_endpoints.py` **1631/1631**.

---

### Fase E1 — Teste de ponta a ponta 🔴 `[depende de: A2, B2]`

**Objetivo:** travar o comportamento onde ele importa — na linha gravada.

**Itens:**
1. `[sequencial]` Em `tests/test_endpoints.py`, adicionar casos com o `TestClient`:
   - requisição auditável **com** `X-Client-Public-IP: 200.1.2.3` ⇒ a linha nova em `audit_log` tem `ip_address == "200.1.2.3"`;
   - **sem** o cabeçalho ⇒ comportamento idêntico ao de hoje (IP de rede);
   - com cabeçalho **privado** (`192.168.0.7`) ou lixo ⇒ ignorado, cai no IP de rede;
   - **rate-limit de login não se move** com o cabeçalho variando (a proteção de D4 fica coberta por teste, não só por comentário).
2. `[sequencial]` Rodar a suíte inteira no Postgres de teste.

**Pronto quando:** `venv/bin/python tests/test_endpoints.py` verde (contagem ≥ 1626 + os casos novos) e `venv/bin/python -m pytest tests/ -q` verde.

#### Status de execução — Fase E1
**Estado:** ✅ Concluída (2026-07-29)
- **O que foi feito:** `tests/test_endpoints.py` — bloco novo no fim da seção "Audit trail (plano 07)", com 5 checagens: cabeçalho válido grava `200.1.2.3`; ausente ⇒ `10.8.200.4` (idêntico a hoje); privado (`192.168.0.7`) ignorado; lixo ignorado; rate-limit de login não se move variando o cabeçalho.
- **Como foi feito / decisões:** a ação auditável escolhida foi `GET /api/audit/export?format=csv` — ela grava a linha `data.export` **de forma síncrona** dentro da request (`routes/audit.py:93`), enquanto os eventos do bus vão por `create_task` e dariam um teste com corrida. O IP é lido pela última linha `data.export` por `max(id)` (o `order_by created_at desc` sozinho empata dentro do mesmo segundo). Todas as requisições mandam `X-Forwarded-For: 10.8.200.4` (a cadeia real medida na instância), então o teste também trava a não-regressão. O caso de D4 usa um bucket de rate-limit isolado (`X-Forwarded-For: 203.0.113.9`) para não interferir nos outros testes de login, e faz 12 tentativas erradas variando o `X-Client-Public-IP` a cada uma: o 429 tem de aparecer mesmo assim.
- **Problemas / pendências:** nenhuma no escopo do plano. **Achados alheios ao plano** (reproduzidos com o trabalho stashed, portanto pré-existentes): (a) `venv/bin/python -m pytest tests/ -q` aborta com `INTERNALERROR ... SystemExit` ao coletar `tests/test_agent_json_hardening.py`, `tests/test_ai_agents_jsonb.py` e `tests/test_coerce_json.py` — são arquivos script-style (`sys.exit` no nível do módulo) que faltam no `collect_ignore` do `tests/conftest.py`; (b) `tests/test_utm_atendente.py` falha (11 casos) e `tests/test_website_widget.py` falha por ordem quando roda a suíte inteira.
- **Verificação:** `venv/bin/python tests/test_endpoints.py` → **1631 passed, 0 failed** (1626 + 5). Suíte pytest verde excluindo os 5 arquivos com problema pré-existente acima.

---

### Fase E2 — Documentação 🟢

**Objetivo:** o próximo a mexer entender por que existe um IP autodeclarado no sistema.

**Itens:**
1. `[paralelo]` Estender o bullet "IP do cliente atrás de proxy reverso" ([CLAUDE.md:837](../CLAUDE.md#L837)): existência do cabeçalho `X-Client-Public-IP`, o fato de ser autodeclarado (D1), a separação auditoria × rate-limit (D4) e a entrada de CSP.
2. `[paralelo]` Registrar que **plugins não precisam fazer nada** (herdam pelo `ActorCtx`) — e que uma screen de plugin que use `fetch()` cru **sem** `authHeaders()` não enviará o cabeçalho.

**Pronto quando:** o bullet descreve o caminho completo sem precisar abrir o código.

#### Status de execução — Fase E2
**Estado:** ✅ Concluída (2026-07-29)
- **O que foi feito:** `CLAUDE.md` — bullet novo **"IP público autodeclarado pelo painel (`X-Client-Public-IP`, plano 86)"** logo abaixo do bullet do plano anterior, nos Gotchas.
- **Como foi feito / decisões:** virou bullet próprio em vez de um parágrafo enfiado no anterior — são dois mecanismos com propósitos opostos (verdade de rede × valor autodeclarado) e misturá-los no mesmo bullet é justamente o erro que D4 quer evitar. O bullet cobre: caminho completo (publicIp.js → authHeaders → audit_ip), a coluna reaproveitada, "plugins não fazem nada" + a exceção do `fetch()` cru, o aviso de forjabilidade, a trava do rate-limit, as limitações (login sem cabeçalho, VPN no meio da sessão), a degradação silenciosa e a nota de privacidade + CSP.
- **Problemas / pendências:** nenhuma.
- **Verificação:** leitura do bullet — descreve o caminho inteiro sem precisar abrir o código.

---

### Fase V1 — Validação na instância real 🔴 `[depende de: tudo]`

**Objetivo:** ver o IP público de verdade na tela `/audit`.

**Itens:**
1. `[sequencial]` Reiniciar o serviço (`whatsbot.service`, porta 8090) — a instância roda `main.py` sem `--reload`.
2. `[sequencial]` Fazer uma ação auditável pelo painel (ex.: salvar uma configuração de plugin) e conferir a linha nova em `/audit`.
3. `[sequencial]` Repetir de uma rede externa (celular em 4G) e confirmar que aparece um IP **diferente** do IP do escritório — é a razão de ser do plano.
4. `[sequencial]` Conferir uma linha gerada por **plugin** (ex.: `protocolos.*`) — deve trazer o mesmo IP sem alteração de plugin.
5. `[sequencial]` Conferir uma linha de ator `system` (ex.: sweep de canal) — deve seguir com o IP de rede/nulo, sem erro.

**Pronto quando:** `/audit` mostra IPs públicos distintos para acesso interno e externo, e nenhuma ação do painel quebra com a rede do serviço externo bloqueada (testar com o domínio bloqueado no navegador).

#### Status de execução — Fase V1
**Estado:** 🟡 Parcial — o que dá para verificar sem navegador está verde; os itens 2–5 dependem do usuário
- **O que foi feito:** conferido que a instância local (`whatsbot.service`, porta 8090) já serve a **CSP nova** (`connect-src 'self' ws: wss: https://www.cloudflare.com`) — o serviço recarregou o código sozinho, não foi preciso reiniciar.
- **Como foi feito / decisões:** a CSP servida prova que o `server/app.py` novo está carregado; como `audit_ip` é importado no topo desse mesmo arquivo, um erro de import teria derrubado o boot.
- **Problemas / pendências:** **os itens 2 a 5 do plano ficam para o usuário** — todos exigem navegador logado e/ou uma segunda rede: (2) ação auditável pelo painel e conferir a linha em `/audit`; (3) repetir de fora (celular em 4G) e ver um IP diferente; (4) conferir uma linha gerada por plugin (`protocolos.*`); (5) conferir uma linha de ator `system`. Também fica pendente a conferência do console sem violação de CSP e do `X-Client-Public-IP` na aba Network (fecha C1/A2/A3).
- **Verificação:** `curl -sI http://127.0.0.1:8090/` → cabeçalho `Content-Security-Policy` com o host novo, demais diretivas idênticas.

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Janela de boot | Login e talvez a 1ª ação saem sem o cabeçalho ⇒ gravam `10.8.200.4` | A3 dispara antes do render. Comportamento **documentado**, não bug. P1 discute persistir entre reloads |
| Serviço externo indisponível/bloqueado | Campo volta ao valor de rede | Degradação silenciosa por construção (A1). Testar explicitamente em V1 com o domínio bloqueado |
| CSP | Afrouxar demais | Host exato numa única diretiva; conferir as demais intactas (C1) |
| Valor forjável | Linha de auditoria pode não refletir a origem real | Aceito em D1. Mitigado só quanto a **lixo**: `is_global` obrigatório (B1) |
| Confusão auditoria × rate-limit | Um refactor futuro "unifica" os helpers e reabre o bypass de login | D4 + comentário nos dois arquivos + **teste** dedicado em E1 |
| IP muda no meio da sessão (VPN ligada/desligada) | Valor fica velho até o próximo reload | Limitação conhecida; uma busca por carregamento de página. Documentar em E2 |
| Escritório com saída única | Todos os operadores internos com o mesmo IP | Esperado e aceito — o objetivo é separar interno × externo, não identificar máquina |
| Privacidade | Cada carregamento do painel revela a um terceiro o IP do operador e o uso do produto | Registrar em E2. Sem PII além do IP; `credentials: 'omit'` |
| Screens de plugin com `fetch()` cru | Não enviam o cabeçalho | Documentar (E2). Convenção já existente é usar `authHeaders()`/`api.http` |
| Modo escuro | — | Nenhuma UI nova neste plano |
| Migration | — | Nenhuma (D3) |

---

## 6. Perguntas em aberto

**P1 — Persistir o IP entre recarregamentos (`sessionStorage`)?**
⏸️ ADIADO. Evitaria uma consulta externa por F5 e fecharia a janela de boot (o login já sairia com o IP). Custo: mais um estado a invalidar quando a rede muda. (a) memória apenas — **recomendado para a v1**; (b) `sessionStorage` com TTL curto. Reavaliar depois de V1, com base em quantas linhas realmente saem com o IP de rede.

**P2 — Guardar também o IP observado na rede, num campo separado?**
⏸️ ADIADO. Hoje o cabeçalho **sobrescreve** e o valor de rede se perde na linha. Se um dia a distinção importar (investigação, suspeita de forja), seria uma coluna nova + migration + coluna na tela. Não fazer agora — D3 pede simplicidade.

**P3 — Aplicar o mesmo valor a `sessions.ip` no login?**
✅ DECIDIDO (2026-07-29): **não**. O login acontece antes de o valor existir (A3), então gravaria o IP de rede de qualquer forma. Reavaliar se P1 for adotada.

**P4 — Evoluir para um valor confiável (eco assinado)?**
⏸️ ADIADO por D6. Desenho já levantado: endpoint de eco por um caminho de rede que preserve o IP de origem, devolvendo o valor + assinatura de curta validade que o backend confere. Só vale a pena se o IP precisar servir como prova.

**P5 — Consertar o hop `10.8.200.4` mesmo assim?**
⏸️ EM ABERTO (infra, fora deste plano). Continua sendo a correção de raiz: sem código, sem terceiro, sem valor autodeclarado. Se um dia for feito, `client_ip()` já resolve a cadeia sozinho — e aí dá para reavaliar se o cabeçalho ainda é necessário.

---

## 7. Apêndice — arquivos-chave

**Backend**
| Arquivo | Papel |
|---|---|
| [server/client_ip.py](../server/client_ip.py) | Resolução de IP — recebe `reported_public_ip()`/`audit_ip()` (B1) |
| [server/app.py:603](../server/app.py#L603) | Ator da auditoria — passa a usar `audit_ip` (B2) |
| [server/app.py:651](../server/app.py#L651) | CSP `connect-src` (C1) |
| [server/routes/auth.py:23](../server/routes/auth.py#L23) | Rate-limit — **não muda** (D4) |
| [server/audit_listener.py:81](../server/audit_listener.py#L81) | Escrita da linha — não muda |
| [db/repositories/audit_repo.py:49](../db/repositories/audit_repo.py#L49) | `add(..., ip_address=...)` — não muda |

**Frontend**
| Arquivo | Papel |
|---|---|
| `web/static/js/services/publicIp.js` | **Novo** — busca + cache + `parseTrace` (A1) |
| `web/static/js/services/publicIp.test.js` | **Novo** — `node --test` do parser (A1) |
| [web/static/js/services/httpClient.js:32](../web/static/js/services/httpClient.js#L32) | `authHeaders()` — seam único do cabeçalho (A2) |
| [web/static/js/app.js:18](../web/static/js/app.js#L18) | Kickoff no boot (A3) |
| [web/static/js/components/AuditLog.js:127](../web/static/js/components/AuditLog.js#L127) | Exibição — não muda (D3) |
| [web/static/js/plugins/api.js:129](../web/static/js/plugins/api.js#L129) | Transporte de plugin — herda por construção |

**Testes / docs**
| Arquivo | Papel |
|---|---|
| [tests/test_client_ip.py](../tests/test_client_ip.py) | Unitário do core (B1) |
| [tests/test_endpoints.py](../tests/test_endpoints.py) | Ponta a ponta (E1) |
| [CLAUDE.md:837](../CLAUDE.md#L837) | Gotcha do IP atrás de proxy (E2) |

---

## 8. Checklist de verificação

- [x] `node --test web/static/js/services/publicIp.test.js` verde — 8/8
- [x] `venv/bin/python -m pytest tests/test_client_ip.py -q` verde — **22/22** (16 antigos + 6 novos)
- [x] `venv/bin/python tests/test_audit.py` verde — 28/28
- [x] `venv/bin/python tests/test_endpoints.py` verde — **1631/1631** (1626 + 5 novos)
- [x] Suíte completa no Postgres de teste — verde **exceto falhas pré-existentes** (reproduzidas com o trabalho stashed): 3 arquivos script-style fora do `collect_ignore` (`test_agent_json_hardening`, `test_ai_agents_jsonb`, `test_coerce_json`) que abortam a coleta, `test_utm_atendente` (11), `test_website_widget` (ordem), `test_multichannel_routing::test_guardrail_no_new_channel_blind_resolvers`, `test_plano68_reopen_assign_on_due::test_build_message_text_includes_scheduler`, `test_plugin_test_discovery` (2). Nenhuma toca o caminho deste plano
- [ ] Console do navegador sem violação de CSP após recarregar o painel — **pendente (V1, usuário)**
- [ ] `X-Client-Public-IP` presente numa chamada do core **e** numa de tela de plugin (aba Network) — **pendente (V1, usuário)**
- [x] Cabeçalho **ausente** ⇒ valor gravado idêntico ao de hoje (sem regressão) — coberto em `test_client_ip.py` e `test_endpoints.py`
- [x] Cabeçalho privado/lixo ⇒ ignorado, cai no IP de rede — coberto nos dois níveis
- [x] Rate-limit de login **não** se move variando o cabeçalho (D4) — teste dedicado em `test_endpoints.py`
- [ ] `/audit` mostra IP público diferente para acesso interno × externo (4G) — **pendente (V1, usuário)**
- [ ] Linha gerada por plugin (`protocolos.*`) traz o mesmo IP, sem alterar plugin — **pendente (V1, usuário)**
- [ ] Linha de ator `system` continua sem erro — **pendente (V1, usuário)**
- [ ] Domínio do serviço externo bloqueado ⇒ painel funciona normalmente — **pendente (V1, usuário)**
- [ ] Reload / back-forward do painel sem erro no console — **pendente (V1, usuário)**
- [x] Sem segredo em URL; nenhuma migration; nenhuma tela nova (modo escuro não se aplica)
