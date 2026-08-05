# Plano 104 — Impedir o navegador de preencher sozinho o "Proxy de saída" (e todo campo secreto de canal)

> **Status:** PLANEJAMENTO · **Data:** 2026-08-05 · **Escopo:** pequeno/médio (frontend genérico + validação opcional no save)
> **Origem:** pedido do usuário — *"já tive dois problemas… o navegador entende como campo de senha e preenche automaticamente; as pessoas salvam sem perceber e o número para de funcionar"*.
> **Método:** leitura do código com `arquivo:linha` verificado (render do campo, caminho de save, validação existente no plugin GOWA).
> O campo "Proxy de saída (opcional)" é renderizado como `<input type="password">` **sem `name` e sem `autocomplete`** — o gerenciador de senhas do navegador injeta a senha do painel ali. Este plano bloqueia o preenchimento automático em **todos** os campos secretos de canal e (opcionalmente) recusa no save um valor que não tenha cara de proxy.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| D1 ✅ (2026-08-05) | O problema é **preenchimento automático do navegador**, não erro de digitação | A correção primária é de atributos de input, não de validação |
| D2 ✅ (2026-08-05) | As pessoas **salvam sem olhar** — a solução não pode depender de o usuário conferir | Por isso a F3 (recusar no save) entra como defesa; a F2 (mostrar/ocultar) é conveniência, nunca a garantia |
| D3 ✅ (2026-08-05) | O campo continua existindo, opcional, no mesmo lugar | Nada de remover, esconder ou mover o proxy para outra tela |
| D4 ✅ (2026-08-05) | O proxy pode conter usuário:senha ⇒ segue sendo credencial `secret` (mascarada na borda da API) | Não virar campo `text` puro; ver P2 |

---

## 1. Resumo executivo

`CredentialField` mapeia todo campo `type: "secret"` para `<input type="password">` sem `name`, sem `id` e sem `autocomplete` ([DescriptorFields.js:94-97](../web/static/js/components/channels/DescriptorFields.js#L94-L97)). Não há sequer um `<form>` em volta — mas o Chrome (e Firefox/Safari, e os gerenciadores de senha) preenchem por **heurística** qualquer campo de senha solto na página do domínio. Resultado: a senha do painel entra no "Proxy de saída", o usuário salva, e o canal passa a carregar um proxy inválido.

O estrago já é contido pelo plugin GOWA — proxy inválido **não** sobe processo dedicado, só escreve `last_error` no canal ([processes.py:123-141](../assets/plugin_examples/gowa/processes.py#L123-L141) e [:258-267](../assets/plugin_examples/gowa/processes.py#L258-L267)) — que é exatamente "a mensagem de erro nessa tela" relatada. **Mas há um caso pior:** se o valor autopreenchido *parecer* uma URL válida, o canal ganha um processo GOWA dedicado, o device é despejado do processo compartilhado e **é preciso ler o QR de novo**.

A correção primária é genérica e vale para todo provider: dizer ao navegador que aquilo não é senha de login. A secundária, opcional, é recusar no save um valor que não case com o formato declarado.

---

## 2. Como funciona hoje (mapa verificado)

| # | Fato | Onde |
|---|---|---|
| 1 | `secret` → `type="password"`; o input **não tem** `name`, `id`, `autocomplete` nem `data-*` de opt-out | [DescriptorFields.js:94-97](../web/static/js/components/channels/DescriptorFields.js#L94-L97) |
| 2 | Não existe `<form>` envolvendo os campos de canal (verificado por grep em `components/channels/` e `ChannelsManager.js`) | — |
| 3 | O `proxy_url` do GOWA é declarado `type: "secret"`, opcional | [gowa_channel.py:101-111](../channels/providers/gowa_channel.py#L101-L111) |
| 4 | Na **edição**, campo em branco = "manter o atual"; segredo mascarado (`••••`) não é pré-preenchido | [DescriptorFields.js:75-81](../web/static/js/components/channels/DescriptorFields.js#L75-L81) + [ChannelEditForm.js:46-54](../web/static/js/components/channels/ChannelEditForm.js#L46-L54) |
| 5 | ⚠️ Logo, um autofill na **edição substitui em silêncio** um proxy que funcionava (ou cria um onde não havia) | consequência de #4 |
| 6 | A rota de criação valida só **presença** de credencial obrigatória — nunca formato | [channels.py:322-332](../server/routes/channels.py#L322-L332), `creation_required_credentials` ([channel_service.py:202-216](../app/services/channel_service.py#L202-L216)) |
| 7 | `proxy_url` **não é obrigatório**, então nem a checagem de presença o toca | [gowa_channel.py:101-111](../channels/providers/gowa_channel.py#L101-L111) |
| 8 | A validação de formato existe, mas **só depois do save**, no reconcile do plugin GOWA (a cada ~15 s) | [processes.py:95-117](../assets/plugin_examples/gowa/processes.py#L95-L117) (`validate_proxy_url`: exige esquema `socks5`/`http`/`https` + host) |
| 9 | Proxy inválido ⇒ **não** entra em `desired_proxies` (não sobe processo dedicado) e escreve `last_error = "Proxy inválido: …"` | [processes.py:123-141](../assets/plugin_examples/gowa/processes.py#L123-L141), [:415-424](../assets/plugin_examples/gowa/processes.py#L415-L424), [:258-267](../assets/plugin_examples/gowa/processes.py#L258-L267) |
| 10 | Proxy **sintaticamente válido** porém falso ⇒ processo dedicado sobe, o device é despejado do compartilhado e **exige QR novo** | comportamento documentado do plano 52 (`CLAUDE.md` → "Proxy de saída por número") |

**Alcance do problema (todos os campos `secret` de canal, não só o proxy):**

| Provider | Campos `secret` sujeitos ao autofill |
|---|---|
| gowa | `proxy_url` |
| telegram | `bot_token` |
| whatsapp_cloud | `access_token`, `app_secret`, `verify_token` |
| facebook_messenger / instagram | `page_access_token`, `app_secret`, `verify_token` |
| website | `hmac_token` |

Uma correção no `CredentialField` cobre todos de uma vez — é o mesmo seam genérico do plano 33 (o core renderiza por `type`, sem saber de qual provider é o campo).

---

## 3. Inventário da mudança

| # | Item | Onde | O que falta | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|---|
| 1 | Opt-out de autofill | [DescriptorFields.js:94-97](../web/static/js/components/channels/DescriptorFields.js#L94-L97) | atributos que dizem "não é senha de login" | `autocomplete="new-password"` (o `off` é **ignorado** pelo Chrome em campo de senha), `name` único e não-semântico, `data-lpignore="true"`, `data-1p-ignore`, `data-bwignore`, `spellcheck={false}` | baixo | **S** |
| 2 | Helper puro testável | [constants.js](../web/static/js/components/channels/constants.js) | os atributos ficariam inline, sem teste | extrair `secretInputProps(fieldKey)` puro e cobrir em [constants.test.js](../web/static/js/components/channels/constants.test.js) (`node --test`) | baixo | **S** |
| 3 | Mostrar/ocultar | [DescriptorFields.js:94-97](../web/static/js/components/channels/DescriptorFields.js#L94-L97) | não dá para conferir o que foi preenchido antes de salvar | botão de olho que alterna `password`↔`text` (D2: quem salva sem olhar ao menos **vê** algo estranho se abrir) | baixo | **S** |
| 4 | Validação no save (defesa) | descriptor + form + rota — ver **P1** | nada valida formato antes de gravar (§2 #6-7) | `credential_fields[].pattern` + `pattern_error` declarados pelo provider e avaliados genericamente | médio | **M** |
| 5 | Rede final (já existe) | [processes.py:95-117](../assets/plugin_examples/gowa/processes.py#L95-L117) | — | **não mexer**; continua sendo a última linha | — | — |

### Falsos positivos descartados

| Suspeita | Por que NÃO é o caminho |
|---|---|
| "Basta `autocomplete="off"`" | Chrome **ignora** `off` em campo de senha desde 2014; o valor suportado para "não preencha aqui" é `new-password` |
| "É porque falta um `<form>`" | Não há form nenhum (§2 #2) e o autofill acontece assim mesmo — a heurística é sobre `type=password`, não sobre o form |
| "Trocar `secret` por `text` resolve" | Exporia o usuário:senha do proxy na tela e mudaria o mascaramento na borda da API (viola D4). Ver P2 |
| "O problema é o plugin GOWA aceitar lixo" | Ele **não** aceita: recusa e escreve `last_error` (§2 #9). O problema é o valor ter sido **gravado** |
| "Precisa validar todos os providers" | Só quem declarar `pattern` ganha validação; sem declaração o comportamento é idêntico ao de hoje |
| "O campo mascarado (`••••`) pode ser reenviado por engano" | Já tratado: a edição não pré-preenche valor mascarado ([ChannelEditForm.js:46-54](../web/static/js/components/channels/ChannelEditForm.js#L46-L54)) |

---

## 4. Fases / Roadmap

```
WAVE 0   F1 (anti-autofill + helper)  ·  F2 (mostrar/ocultar)     🟢 independentes
              │
              └── [bloqueia: F4]
WAVE 1   F3 (validação de formato — condicionada à P1)             🔴 sozinha
WAVE 2   F4 (testes + doc)                                          🔴 [depende de: F1, F3]
```

| Wave | Fase | Workstream | Paraleliza? | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F1** | Frontend — bloquear o autofill | 🟢 | baixo | recarregar a tela e o campo vir **vazio**, mesmo com senha salva no navegador |
| 0 | **F2** | Frontend — mostrar/ocultar segredo | 🟢 | baixo | o olho alterna e o valor é legível antes de salvar |
| 1 | **F3** | Descriptor + form + rota — recusar formato inválido | 🔴 sozinha | médio | salvar "minhasenha123" no proxy dá erro claro, sem gravar |
| 2 | **F4** | Testes puros + doc | 🔴 sozinha | baixo | `node --test` verde + `CLAUDE.md` atualizado |

---

### F1 — Bloquear o preenchimento automático (🟢) — **é o que resolve o pedido**

**Objetivo:** o navegador e os gerenciadores de senha pararem de injetar valor nos campos secretos de canal.

**Itens:**
1. `[sequencial]` [constants.js](../web/static/js/components/channels/constants.js): criar o helper **puro** `secretInputProps(fieldKey)` devolvendo o pacote de atributos — `autocomplete: 'new-password'`, `name` estável e não-semântico derivado da chave (ex.: `ch-<key>-secret`, jamais `password`/`senha`/`token`), `data-lpignore: 'true'`, `data-1p-ignore: ''`, `data-bwignore: 'true'`, `spellcheck: false`, `autocapitalize: 'off'`, `autocorrect: 'off'`.
2. `[sequencial]` [DescriptorFields.js:94-97](../web/static/js/components/channels/DescriptorFields.js#L94-L97): espalhar esses atributos no input quando `type === 'secret'`. Campo `text`/`token_suggest` fica como está (não é alvo do gerenciador de senha).
3. `[paralelo]` Conferir se o `token_suggest` ([DescriptorFields.js:83-92](../web/static/js/components/channels/DescriptorFields.js#L83-L92)) também sofre autofill em algum navegador; se sim, aplicar o mesmo pacote (é `type=text`, então normalmente não).

⚠️ **Não usar** `autocomplete="off"` (ignorado em campo de senha) nem o truque de `readonly` até o foco (quebra colar em alguns navegadores e atrapalha leitor de tela).

**Pronto quando:** com uma senha salva no navegador para o domínio do painel, abrir **Canais → Novo canal → GOWA** e **Canais → Editar** e ver o campo "Proxy de saída" **vazio**, sem sugestão de preenchimento; o mesmo para `bot_token` (Telegram) e `access_token` (Cloud). Testar em Chrome **e** em ao menos um outro navegador.

#### Status de execução — Fase 1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(atributos escolhidos; navegadores testados)_
- **Problemas / pendências:** _(navegador/gerenciador que ainda preenche)_
- **Verificação:** _(teste manual por navegador + `node --test`)_

---

### F2 — Mostrar/ocultar o segredo (🟢, opcional mas recomendada)

**Objetivo:** dar ao operador uma forma de **ver** o que está no campo antes de salvar (D2).

**Itens:**
1. `[sequencial]` [DescriptorFields.js:94-97](../web/static/js/components/channels/DescriptorFields.js#L94-L97): botão ao lado do input alternando `type` entre `password` e `text`, com `aria-label` ("Mostrar valor" / "Ocultar valor") e `type="button"` (não submete nada).
2. `[paralelo]` Estilo com classes semânticas `wa-*` (`text-wa-secondary`, `hover:bg-wa-hover`, `border-wa-border`) — conferir no **modo escuro** (regra do `CLAUDE.md`).
3. `[paralelo]` Não alternar para `text` automaticamente e nunca persistir a escolha: cada abertura do formulário começa oculto.

**Pronto quando:** o olho alterna a visibilidade, funciona em criação e edição, e a tela segue legível no tema escuro.

#### Status de execução — Fase 2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(escolhas de UI/acessibilidade)_
- **Problemas / pendências:** _(o que ficou para depois)_
- **Verificação:** _(claro + escuro; criação + edição)_

---

### F3 — Recusar formato inválido no save (🔴 sozinha) [condicionada à P1]

**Objetivo:** mesmo que algum navegador burle a F1, um valor que não é proxy **não chega ao banco**.

**Itens:**
1. `[sequencial]` Descriptor: acrescentar `pattern` (regex) + `pattern_error` (mensagem PT-BR) ao `credential_fields` do `proxy_url` em [gowa_channel.py:101-111](../channels/providers/gowa_channel.py#L101-L111) — algo equivalente ao que `validate_proxy_url` já exige: esquema `socks5://`/`http://`/`https://` + host não vazio.
2. `[sequencial]` Avaliação **no cliente**: função pura em [constants.js](../web/static/js/components/channels/constants.js) (ex.: `validateCredentials(descriptor, credValues)` → `{key: mensagem}`), consumida por `ChannelForm`/`ChannelEditForm` para bloquear o botão Salvar e mostrar o erro sob o campo.
3. `[sequencial]` Avaliação **no servidor** (o servidor não confia no cliente): mesma checagem na criação ([channels.py:322-332](../server/routes/channels.py#L322-L332)) e na atualização, retornando **400** com a mensagem do `pattern_error`. Genérico, dirigido pelo descriptor — **sem `if provider ==`** no core.
4. `[paralelo]` Campo vazio continua válido (o proxy é opcional; e na edição vazio = "manter").
5. `[paralelo]` Não tocar em [processes.py:95-117](../assets/plugin_examples/gowa/processes.py#L95-L117) — segue como rede final para rows legadas.

**Pronto quando:** tentar salvar `minhasenha123` no proxy mostra erro no formulário **e**, se o POST for forçado por `curl`, a API responde 400 sem gravar; salvar `socks5://user:pass@1.2.3.4:1080` continua funcionando; salvar vazio continua mantendo o atual.

#### Status de execução — Fase 3
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(P1 decidida como (a)/(b)/(c) e por quê)_
- **Problemas / pendências:** _(o que ficou para depois)_
- **Verificação:** _(teste de formulário + `curl` direto na API + suíte)_

---

### F4 — Testes e documentação (🔴 sozinha) [depende de: F1, F3]

**Objetivo:** travar o comportamento e registrar o porquê.

**Itens:**
1. `[sequencial]` [constants.test.js](../web/static/js/components/channels/constants.test.js) (`node --test`): `secretInputProps` devolve `autocomplete === 'new-password'`, `name` sem as palavras `password`/`senha`/`token`, e os `data-*` de opt-out. Se a F3 entrar: casos de `validateCredentials` (válido, inválido, vazio).
2. `[paralelo]` Se a F3 entrar: teste de API — `POST /api/channels` com `proxy_url` inválido ⇒ 400 e **nenhuma** row criada; `PUT` idem, preservando a credencial anterior.
3. `[paralelo]` `CLAUDE.md` → seção **"Proxy de saída por número (plano 52)"**: uma linha explicando que o campo bloqueia autofill (e por quê), apontando `DescriptorFields.js`.
4. `[sequencial]` Rodar `node --test` nos módulos puros e `venv/bin/python -m pytest tests/integration tests/contracts` no Postgres de teste.

**Pronto quando:** testes verdes e a seção do `CLAUDE.md` explicando o motivo do atributo (para ninguém "limpar" o `new-password` achando que é resquício).

#### Status de execução — Fase 4
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(escolhas de cobertura)_
- **Problemas / pendências:** _(falhas pré-existentes vs regressões)_
- **Verificação:** _(comandos + contagem verde/vermelho)_

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Gerenciador de senha externo (1Password/LastPass/Bitwarden) | Ignora `autocomplete` e preenche assim mesmo | Por isso os `data-lpignore` / `data-1p-ignore` / `data-bwignore` (F1 item 1) **e** a F3 como defesa — nenhuma camada sozinha é garantia |
| `name` mal escolhido | Um `name="password"` reativa a heurística que se quer evitar | Nome derivado da chave e proibição explícita das palavras-gatilho (F1 item 1 + teste da F4) |
| Regressão no colar | Truques do tipo `readonly`-até-focar quebram colar/leitor de tela | Descartado explicitamente na F1 |
| F3 rígida demais | Um `pattern` mal escrito recusa proxy legítimo (IPv6, porta ausente, credencial com `@`) | Espelhar `validate_proxy_url` ([processes.py:95-117](../assets/plugin_examples/gowa/processes.py#L95-L117)), que já é a regra em produção; testar IPv6 e usuário:senha com caracteres especiais |
| F3 e rows legadas | Um canal já salvo com proxy inválido passaria a falhar na próxima edição de **qualquer** campo | Validar só o que foi **submetido** (na edição, vazio = manter ⇒ não revalida o armazenado) |
| Escopo do core | `pattern` é campo novo no contrato de descriptor | É genérico (todo provider pode usar), avaliado pelo core, sem `if provider ==` — mesmo padrão de `credential_fields[].required` e de `MediaLimits`. Ver P1 |
| Modo escuro | Botão de mostrar/ocultar novo na tela | Classes `wa-*`, conferir nos dois temas (F2 item 2) |
| Segredo em log/URL | Um erro 400 que ecoe o valor recusado vazaria a senha do usuário no log | A mensagem de erro cita **o campo**, nunca o valor |

---

## 6. Perguntas em aberto

**P1 — Onde declarar/avaliar a validação de formato da F3?**
⏸️ **DECIDIR ANTES DA F3.**
(a) **`credential_fields[].pattern` + `pattern_error` no descriptor**, avaliado pelo form genérico e pela rota. Genérico, uma declaração e duas avaliações, mesmo formato do `required` que já existe.
(b) Hook `Channel.validate_credentials(creds) -> dict[str,str] | None` (classmethod, default no-op), no estilo de `identity_from_credentials`. Mais poderoso (validação cruzada entre campos), mais superfície de core.
(c) Só validação no cliente, sem servidor. Mais barato; não protege contra POST direto.
**Recomendação:** (a). Cobre o caso real com o menor contrato novo e não exige que o core saiba o que é um proxy. Reabrir para (b) se algum provider precisar validar dois campos juntos.

**P2 — O `proxy_url` deveria deixar de ser `secret`?**
⏸️ **ADIADO — provavelmente não.** Contexto: campo `text` puro nunca é alvo do gerenciador de senha, o que mataria o problema na raiz. Mas a URL pode conter `usuario:senha` e hoje é mascarada na borda da API ([channel_service.py:288](../app/services/channel_service.py#L288) e `_public_cred_keys`), o que viola a D4.
(a) Manter `secret` + F1 + F2 (mostrar/ocultar dá a mesma conferência visual sem expor por padrão).
(b) Virar `text` — simples, porém expõe a credencial na tela e muda o mascaramento.
**Recomendação:** (a).

**P3 — A F1 sozinha basta?**
✅ **DECIDIDO (2026-08-05): a F1 é o que resolve o pedido e pode ir para produção sozinha.** A F3 é defesa em profundidade e pode vir depois, num commit próprio — inclusive porque tem risco médio (P1) enquanto a F1 tem risco baixo. Não bloquear a F1 esperando a F3.

---

## 7. Apêndice — arquivos-chave

| Camada | Arquivo | Papel |
|---|---|---|
| Frontend (render) | [web/static/js/components/channels/DescriptorFields.js:75-106](../web/static/js/components/channels/DescriptorFields.js#L75-L106) | **edita** — `CredentialField`: atributos anti-autofill (F1) + mostrar/ocultar (F2) |
| Frontend (puro) | [web/static/js/components/channels/constants.js](../web/static/js/components/channels/constants.js) | **edita** — `secretInputProps` (F1) e, se a P1 for (a), `validateCredentials` (F3) |
| Frontend (formulários) | [ChannelForm.js](../web/static/js/components/channels/ChannelForm.js), [ChannelEditForm.js:46-54](../web/static/js/components/channels/ChannelEditForm.js#L46-L54) | F3 — bloquear Salvar e exibir o erro |
| Backend (provider) | [channels/providers/gowa_channel.py:101-111](../channels/providers/gowa_channel.py#L101-L111) | F3 — declarar `pattern`/`pattern_error` do `proxy_url` |
| Backend (rota) | [server/routes/channels.py:322-332](../server/routes/channels.py#L322-L332) | F3 — avaliar no create/update, retornar 400 |
| Plugin (rede final) | [assets/plugin_examples/gowa/processes.py:95-117](../assets/plugin_examples/gowa/processes.py#L95-L117), [:258-267](../assets/plugin_examples/gowa/processes.py#L258-L267) | **não mexer** — validação pós-save já existente |
| Testes | [web/static/js/components/channels/constants.test.js](../web/static/js/components/channels/constants.test.js) | `node --test` dos helpers puros |
| Doc | `CLAUDE.md` → "Proxy de saída por número (plano 52)" | registrar o porquê do `new-password` |

---

## 8. Checklist de verificação

- [ ] Com senha salva no navegador, o campo "Proxy de saída" abre **vazio** na criação **e** na edição
- [ ] Idem para `bot_token` (Telegram) e `access_token`/`app_secret` (Cloud)
- [ ] Testado em Chrome e em pelo menos um outro navegador
- [ ] Nenhum input secreto tem `name` contendo `password`/`senha`/`token`
- [ ] Colar (Ctrl+V) continua funcionando no campo
- [ ] Botão mostrar/ocultar legível no **modo escuro** (se a F2 entrar)
- [ ] Salvar proxy válido (`socks5://user:pass@ip:porta`) continua funcionando de ponta a ponta
- [ ] Edição com campo vazio continua significando "manter o atual" (não apaga o proxy salvo)
- [ ] Se a F3 entrar: valor inválido é recusado no formulário **e** por `curl` direto na API (400, nada gravado)
- [ ] Se a F3 entrar: mensagem de erro cita o campo, **nunca** o valor recusado (não vazar senha em log)
- [ ] `node --test` verde nos módulos puros de canais
- [ ] `venv/bin/python -m pytest tests/integration tests/contracts` verde no Postgres (`WHATSBOT_TEST_DB_URL`)
- [ ] `CLAUDE.md` registra o motivo do `autocomplete="new-password"`
