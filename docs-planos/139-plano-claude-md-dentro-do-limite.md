# Plano 139 — CLAUDE.md dentro do limite: mover o "porquê" para `docs/` e travar o crescimento

> **Status:** ✅ EXECUTADO (2026-08-24) · **Data:** 2026-08-24 · **Escopo:** médio-grande (documentação + 2 guardas de teste, zero mudança de runtime)
> **Resultado:** `CLAUDE.md` **188.104 → 66.963 chars (−64%)**, 2.078 fatos preservados e verificados por máquina, teto travado em 90.000.
> **Origem:** aviso do Claude Code — `⚠ CLAUDE.md is over the 150.0k-char limit (188.1k chars)`. Pedido do usuário: caber no limite **sem perder qualidade nem informação importante**.
> **Método:** medição direta (`python3 len()`, contagem por seção via parser de headings, `git show <rev>:CLAUDE.md`), leitura integral do arquivo, varredura de back-references (`grep -rn "CLAUDE.md"`), inspeção do catálogo executável (`plugins/events.py`) e dos guias já existentes em `docs/`.
> O arquivo tem **188.104 caracteres** e cresce **~2.500–2.900 chars/dia**. Nada aqui é apagado: o texto integral migra para guias temáticos em `docs/`, e o `CLAUDE.md` fica com a **regra + o tripwire + o ponteiro**. Junto vai a parte que importa mais que o corte: uma **política de escrita** e **duas guardas automatizadas**, para o arquivo não voltar a estourar em ~5 semanas.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| **D1** | ✅ (2026-08-24, usuário) **Nada é apagado.** Todo parágrafo, tabela, número e história de bug sobrevive — muda só *onde* mora. | O corte é feito por **extração**, não por poda. Fase 0 cria uma guarda mecânica que prova a preservação (§8.2). |
| **D2** | ✅ Destino = **`docs/`**, nunca `docs-planos/`. | `docs-planos/` é **podado** depois que o plano executa (ver `git status`: 8 planos deletados; commit `344a08f` "registra o plano executado antes da poda"). Documento que precisa durar não pode morar lá. |
| **D3** | ✅ Os ponteiros são **links markdown normais**, nunca `@import`. | O `@arquivo.md` do Claude Code é **inlinado** no contexto — a redução seria fictícia. Hoje o `CLAUDE.md` não tem nenhum `@import` (verificado); continuar assim. |
| **D4** | ✅ **Todo `⚠️` / `🚫` continua no `CLAUDE.md`**, como uma linha imperativa + link. | São 36 linhas (§3.4). São elas que impedem uma sessão futura de "consertar" o que está certo. O que migra é a *narrativa*, não o aviso. |
| **D5** | ✅ Zero mudança de runtime nesta entrega. | Só `.md`, mais dois arquivos de teste e um script. Suíte tem de ficar verde sem nenhuma alteração de código de produção. |
| **D6** | ✅ O corte sozinho **não** é a solução. | Ao ritmo atual, mesmo um arquivo de 61k volta ao limite em ~5 semanas. A Fase 10 (política + guarda de tamanho) é **obrigatória**, não opcional. |

---

## 1. Resumo executivo

O `CLAUDE.md` virou o **único** lugar onde o projeto registra decisão de arquitetura, e cada plano executado escreve mais um parágrafo nele. Em 22 dias ele saiu de 124.975 para 188.104 chars (**+2.869/dia**) e passou o teto de 150k do Claude Code — o aviso é sobre **contexto**, não truncamento: os ~188k chars (≈ 59k tokens) entram em **toda** requisição de **toda** sessão.

A forma da solução tem três alavancas, nesta ordem de valor:

1. **Extrair** (≈ 108k chars): 30 seções que são *referência* ou *narrativa de plano* saem inteiras para 7 guias em `docs/`, deixando no `CLAUDE.md` um resumo de 400–2.000 chars com a regra e os tripwires. Zero perda — o texto muda de arquivo.
2. **Comprimir** (≈ 18k chars): 34 linhas do arquivo têm ≥ 800 chars cada e somam 40.382 chars (21% do total). São bullets-ensaio com três camadas (regra / mecanismo / história); só a história viaja.
3. **Travar** (o que evita a recaída): política explícita de onde escrever + `tests/contracts/test_docs_hygiene.py` com teto de tamanho e prova de cobertura dos fatos.

Estimativa: **188.104 → ~61.400 chars** (−68%). O contrato de aceite é mais folgado que a estimativa — **≤ 90.000** — para o executor poder manter mais texto onde julgar necessário sem falhar a guarda.

---

## 2. Como está hoje (medido)

### 2.1 Crescimento

| Data | Revisão | Chars | Δ/dia desde a anterior |
|---|---|---:|---:|
| 2026-03-19 | `7a42134` | 4.898 | — |
| 2026-03-25 | `19888c8` | 9.085 | +698 |
| 2026-07-29 | `8320353` | 124.975 | +916 |
| 2026-07-31 | `db4aca3` | 148.154 | **+11.590** |
| 2026-08-07 | `fbafd05` | 156.089 | +1.134 |
| 2026-08-20 | `921da9f` | **188.104** | **+2.463** |

O padrão do `git log --numstat` é monotônico: praticamente todo commit de feature **adiciona** linhas (12, 16, 17, 19, 38, 40, 80…) e quase nenhum remove. Não há mecanismo de poda.

⚠️ **Consequência aritmética que define o plano:** a ~2.500 chars/dia, cortar para 149k compra **~15 dias**; cortar para 90k compra **~24 dias**; cortar para 61k compra **~36 dias**. Nenhum corte, sozinho, resolve — só a política da Fase 10 resolve.

### 2.2 Distribuição

| Bloco | Chars | % |
|---|---:|---:|
| `## Sistema de plugins` (H2 inteira) | 59.617 | 32% |
| └ `### Events e Filters (bus do plugin)` | 18.466 | 10% |
| `## Gotchas` | 13.549 | 7% |
| └ 4 bullets-ensaio (L1079, L1082, L1083, L1081) | 7.736 | 4% |
| `## Canais Meta` | 11.009 | 6% |
| `## Provider de canal (plugin)` | 8.428 | 4% |
| Linhas de tabela (195 linhas) | 26.037 | 14% |
| Blocos de código | 6.330 | 3% |

### 2.3 As 34 linhas-ensaio

34 linhas têm ≥ 800 chars e somam **40.382 chars (21% do arquivo)**. As cinco maiores:

| Chars | Linha | Assunto |
|---:|---|---|
| 2.806 | L1079 | `statics/` precisa de pasta persistente no deploy |
| 2.130 | L1082 | IP público autodeclarado pelo painel (`X-Client-Public-IP`) |
| 1.919 | L408 | Agente padrão / fallback unificado |
| 1.690 | L1083 | Echo do próprio envio: quem cala é o provider |
| 1.422 | L201 | Assinatura `X-Hub-Signature-256` |

Cada uma tem a mesma estrutura de três camadas — e é isso que torna a compressão segura e mecânica:

```
(a) REGRA      → "NUNCA declare VOLUME no Dockerfile; use bind mount / Persistent Storage"   ← FICA no CLAUDE.md
(b) MECANISMO  → arquivo:linha de quem implementa                                            ← FICA em 1 linha
(c) HISTÓRIA   → por que quebrou, o que foi medido, o que enganava                           ← VAI para docs/
```

### 2.4 Tripwires (o ativo a preservar)

**36 linhas** com `⚠️` ou `🚫` (33 + 3). São o conteúdo de maior valor do arquivo — cada uma existe porque alguém já "consertou" algo que estava certo. Ver a lista completa em §9.1; nenhuma pode sair do `CLAUDE.md` (D4).

### 2.5 Duplicação já existente (evidência de que a extração é segura)

| Conteúdo no `CLAUDE.md` | Já existe também em | Observação |
|---|---|---|
| `### Events e Filters` — tabelas de eventos/filtros (18.466) | `plugins/events.py` (674 linhas, `KNOWN_EVENTS`/`KNOWN_FILTERS` **com comentários semânticos por filtro**) e `.claude/commands/new-plugin.md:357+` | O próprio `new-plugin.md:18` diz: *"a tabela do `CLAUDE.md` é guia de payloads, **não catálogo exaustivo**"* — a fonte de verdade já é o código |
| `### Auditoria de plugins` (3.683) | `docs/PLUGINS_AUDITAVEIS.md` (12.396, 11 seções + checklist) | O guia é mais completo que o resumo |
| `### Versionamento da API de plugins` (4.353) | `docs/PLUGIN_API_CHANGELOG.md` (24.357) + `plugins/semver.py` | O changelog já é a fonte |
| Colunas de `messages`/`contacts` na `### Tabelas` (célula única de 939 chars) | `docs/analises/01-modelo-de-dados.md` (42.790) | Modelo de dados já documentado |
| `statics/` persistente (2.806) | `docs/DEPLOY_COOLIFY.md` (4.656) — **já linkado de dentro do próprio bullet** | O destino natural já existe |
| Convenções de plugin, `plugin.yaml`, RBAC, auditoria, modo escuro | `.claude/commands/new-plugin.md` (30.211) e `new-channel.md` (16.415) | Carregam **sob demanda**, não em todo turno |

### 2.6 Achado colateral

`CLAUDE.md` cita `tests/integration/test_channel_credential_pattern.py` (§"Proxy de saída", plano 104 F3) — **o arquivo não existe** no disco. A varredura de caminhos encontrou 148 caminhos citados; 15 não resolvem (a maioria é caminho relativo do repositório de plugins ou artefato do regex, mas este é uma referência morta de verdade). A guarda da Fase 0 passa a detectar isso.

---

## 3. Falsos positivos descartados

| Hipótese | Por que NÃO é o caminho |
|---|---|
| "O arquivo é grande porque o projeto é grande — é o preço." | Refutado pela medição: 21% do arquivo está em **34 linhas**, e 32% numa única seção cujo catálogo já vive no código (`plugins/events.py`). O tamanho é de *narrativa*, não de *cobertura*. |
| "Basta apagar as seções de planos antigos (12, 21, 29, 32, 33…)." | Viola D1 e destrói o ativo: essas seções são justamente onde moram os tripwires. Apagar é o único caminho que perde qualidade de verdade. |
| "Usar `@docs/foo.md` no `CLAUDE.md`." | O `@import` é inlinado no contexto — o contador cairia só se o Claude Code não contasse imports, o que **não** dá para assumir. Redução fictícia com risco de regressão silenciosa. Ver D3. |
| "Transformar tudo em `.claude/skills/`." | Skills resolvem descoberta, mas o repositório já tem a convenção `docs/` + link, com 3 guias funcionando (`PLUGINS_AUDITAVEIS`, `PLUGIN_API_CHANGELOG`, `DEPLOY_COOLIFY`) e citados de dentro do código-fonte. Trocar de mecanismo agora acrescenta risco sem ganho. (Pode virar plano próprio depois — ver P3.) |
| "Mover o conteúdo para `docs-planos/`." | `docs-planos/` é podado após execução (D2). Seria perder informação num prazo de semanas. |
| "Só reduzir o `## Gotchas`, que é o mais 'solto'." | Gotchas é 7%. Mesmo zerado, o arquivo ficaria em 174k — ainda acima do limite. |

---

## 4. Mapa de destino (`docs/`)

| Arquivo | Estado | Recebe | Tamanho estimado |
|---|---|---|---|
| `docs/PLUGIN_BUS.md` | **novo** | Events e Filters (tabelas completas, assinaturas, padrões de uso, boas práticas) + Media types suportados | ~21k |
| `docs/PLUGINS.md` | **novo** | Regra core-vs-plugin (íntegra), onde fica a configuração, frontend dinâmico, override de componente, RBAC, `entry.services`, import/export, lifecycle detalhado | ~30k |
| `docs/CANAIS.md` | **novo** | Provider descriptor, identidade/dedup (32), proxy de saída (52), filtro de JID, limites de mídia, tipo de contato | ~25k |
| `docs/CANAIS_META.md` | **novo** | Messenger/Instagram (46/76/121), janelas de 24h/7d/IA, alertas da conta Meta (84) | ~15k |
| `docs/IA.md` | **novo** | Motor AGNO, guardrails/routing, gate humano (96), despedida (122), IA por canal (21), filtro de histórico (43), onboarding Techify | ~20k |
| `docs/UI_CONVERSA.md` | **novo** | Bandeja/compositor (124), rascunho, janela ancorada (99), digitação entre atendentes, avisos de sistema (12) | ~20k |
| `docs/API_REST.md` | **novo** | Tabela completa de endpoints REST + catálogo de eventos WebSocket | ~7k |
| `docs/OPERACAO.md` | **novo** | Gotchas longos que não são de deploy: IP atrás de proxy, IP autodeclarado, echo do próprio envio, debug do GOWA, HSM/linked device | ~8k |
| `docs/TESTES.md` | **novo** | Suíte por camada, banco de teste, runner de plugins, Evolution API | ~5k |
| `docs/FRONTEND.md` | **novo** | Tema/modo escuro detalhado, tokens `wa-*`, `themeContrast.js` | ~3k |
| `docs/DEPLOY_COOLIFY.md` | existe | Absorve a íntegra do gotcha de `statics/`/`storages/` persistentes | 4.656 → ~8k |
| `docs/PLUGINS_AUDITAVEIS.md` | existe | Absorve o que o resumo do `CLAUDE.md` tem a mais | 12.396 → ~13k |
| `docs/PLUGIN_API_CHANGELOG.md` | existe | Absorve a política de MAJOR/MINOR/PATCH e o histórico do congelamento em 1.0.0 | 24.357 → ~28k |
| `docs/analises/01-modelo-de-dados.md` | existe | Absorve o detalhamento de colunas de `messages`/`contacts` | 42.790 → ~45k |

**Regra de escrita dos guias:** cada um abre com `> Guia de <área>. O `CLAUDE.md` carrega a regra curta; aqui está o porquê, o histórico e os detalhes.` e é organizado por plano/assunto, com os `arquivo:linha` preservados **verbatim**.

**Regra de escrita do resíduo no `CLAUDE.md`** (o formato do bloco que fica):

```markdown
## <Assunto> → [docs/AREA.md](docs/AREA.md)

<1–3 frases: o que é e a regra dura, em imperativo.>
⚠️ <tripwire, uma linha, imperativo> — o porquê está no guia.
```

---

## 5. Inventário — o que fica no `CLAUDE.md`

Somatório verificado: as duas tabelas (§5 e §6) cobrem as 1.089 linhas e somam exatamente 188.105 chars.

| Linhas | Seção | Hoje | Ação | Destino do detalhe | Fica |
|---|---|---:|---|---|---:|
| 1-4 | Cabeçalho + decisão de distribuição | 367 | manter | — | 367 |
| 5-16 | Stack | 1.599 | comprimir | — | 1.200 |
| 17-60 | Arquitetura (mapa de arquivos) | 3.265 | manter | — | 3.100 |
| 61-89 | Comandos / launchers | 1.682 | comprimir | — | 1.400 |
| 90-151 | Banco de dados (URL + Tabelas + Padrão) | 5.823 | comprimir | `docs/analises/01-modelo-de-dados.md` | 3.300 |
| 152-164 | Fluxo de mensagens (webhook) | 895 | manter | — | 895 |
| 296-309 | Memória por contato | 818 | manter | — | 818 |
| 380-392 | Provider de LLM e onboarding (Techify) | 1.801 | comprimir | `docs/IA.md` | 1.000 |
| 443-446 | Fotos de perfil | 594 | manter | — | 594 |
| 447-455 | @menções em grupos | 1.266 | manter | — | 1.266 |
| 507-532 | GOWA REST API | 1.891 | manter | — | 1.891 |
| 533-545 | Convenções de código | 2.013 | manter | — | 2.013 |
| 546-560 | Tema e modo escuro | 2.490 | comprimir | `docs/FRONTEND.md` | 1.400 |
| 561-569 | Dados do projeto | 690 | manter | — | 690 |
| 570-611 | Sistema de plugins (intro/layout/lifecycle/settings) | 3.261 | comprimir | `docs/PLUGINS.md` | 2.200 |
| 683-696 | Convenções obrigatórias (plugin) | 2.523 | comprimir | — | 2.000 |
| 927-941 | Onde vive o código de um plugin | 2.478 | comprimir (manter a tabela dos 4 lugares) | — | 1.500 |
| 966-969 | Criar um plugin novo | 343 | manter | — | 343 |
| 970-975 | Importar/exportar | 707 | comprimir | `docs/PLUGINS.md` | 450 |
| 976-1018 | Testes automatizados | 2.830 | comprimir | `docs/TESTES.md` | 1.600 |
| 1019-1059 | Teste opcional com Evolution API | 1.564 | mover | `docs/TESTES.md` | 250 |
| 1060-1089 | Gotchas | 13.549 | dividir | `docs/OPERACAO.md` + `docs/DEPLOY_COOLIFY.md` | 5.100 |
| (novo) | **Índice de docs — quando ler cada um** | 0 | criar | — | 1.200 |
| | **Subtotal** | **52.449** | | | **34.577** |

⚠️ **`## Arquitetura` (o mapa de arquivos) é intocável.** É o que faz uma sessão nova achar `agent/agno_engine.py` sem varrer o repositório. Comprimir só remove comentários redundantes de linha, nunca uma entrada.

---

## 6. Inventário — o que migra (resumo + ponteiro fica)

| Linhas | Seção | Hoje | Destino do texto integral | Resíduo | Risco |
|---|---|---:|---|---:|---|
| 782-926 | Events e Filters (bus) | 18.466 | `docs/PLUGIN_BUS.md` | 2.000 | baixo — catálogo já vive em `plugins/events.py` |
| 197-224 | Canais Meta (46/76/121) | 11.009 | `docs/CANAIS_META.md` | 1.400 | **alto** — 4 tripwires densos (L204, L215, L217, L219) |
| 239-253 | Provider de canal / descriptor (33) | 8.428 | `docs/CANAIS.md` | 1.500 | médio — regra "sem `if provider ==`" fica |
| 456-506 | API REST + eventos WS | 6.214 | `docs/API_REST.md` | 900 | baixo — derivável de `server/routes/` |
| 339-356 | Bandeja de anexo / legenda (124) | 5.740 | `docs/UI_CONVERSA.md` | 700 | médio — 2 tripwires (L352, L355) |
| 612-640 | O que fica no core vs plugin (REGRA) | 5.375 | `docs/PLUGINS.md` | **1.800** | **alto** — é a regra de decisão mais citada do repo |
| 357-368 | Janela ancorada / busca / data (99) | 5.272 | `docs/UI_CONVERSA.md` | 700 | médio — tripwire L366 |
| 254-264 | Proxy de saída por número (52) | 4.709 | `docs/CANAIS.md` | 700 | médio — tripwire de autofill (L262) |
| 393-409 | Motor de agente (AGNO) | 4.582 | `docs/IA.md` | 1.500 | médio |
| 766-781 | API interna plugin→plugin | 4.413 | `docs/PLUGINS.md` | 1.000 | baixo |
| 697-723 | Versionamento da API de plugins | 4.353 | `docs/PLUGIN_API_CHANGELOG.md` | 1.400 | médio — tabela MAJOR/MINOR/PATCH fica |
| 225-238 | Alertas da conta Meta (84) | 4.323 | `docs/CANAIS_META.md` | 500 | baixo |
| 285-295 | Configuração de IA por canal (21) | 4.313 | `docs/IA.md` | 1.000 | médio — `_NO_SEED` de `image_transcription_mode` fica |
| 326-338 | Rascunho por conversa | 3.977 | `docs/UI_CONVERSA.md` | 500 | baixo |
| 419-430 | Humano no comando cala a IA (96) | 3.693 | `docs/IA.md` | 900 | **alto** — ordem época-antes-do-gate |
| 724-742 | Auditoria de plugins | 3.683 | `docs/PLUGINS_AUDITAVEIS.md` | 800 | baixo — guia já existe |
| 182-196 | Identidade de conta / dedup (32) | 3.653 | `docs/CANAIS.md` | 700 | baixo |
| 641-655 | Onde fica a configuração de um plugin | 3.208 | `docs/PLUGINS.md` | 900 | médio — a REGRA fica |
| 656-668 | Frontend dinâmico | 2.982 | `docs/PLUGINS.md` | 900 | médio — 🚫 do `new WebSocket('/ws')` fica |
| 743-765 | RBAC de plugins | 2.890 | `docs/PLUGINS.md` | 900 | baixo |
| 410-418 | Guardrails e routing (29 A/B) | 2.737 | `docs/IA.md` | 800 | médio |
| 369-379 | Digitação entre atendentes | 2.720 | `docs/UI_CONVERSA.md` | 400 | baixo |
| 317-325 | Avisos de sistema no chat (12) | 2.634 | `docs/UI_CONVERSA.md` | 600 | baixo |
| 942-965 | Media types suportados | 2.610 | `docs/PLUGIN_BUS.md` | 500 | baixo — 🚫 fica |
| 265-274 | Limites de mídia por canal | 2.477 | `docs/CANAIS.md` | 600 | baixo |
| 275-284 | Tipo de contato por canal | 2.450 | `docs/CANAIS.md` | 600 | baixo |
| 669-682 | Override de componente (92) | 2.357 | `docs/PLUGINS.md` | 600 | baixo |
| 165-181 | Filtro de tipos de JID (GOWA) | 2.353 | `docs/CANAIS.md` | **900** | **alto** — a tabela dos DOIS defaults fica inteira |
| 431-442 | A IA se despede ao transferir (122) | 2.170 | `docs/IA.md` | 600 | **alto** — 226 transferências mudas em produção |
| 310-316 | Filtro de histórico por regex (43) | 1.865 | `docs/IA.md` | 500 | baixo |
| | **Subtotal** | **135.656** | | **26.800** | |

**Total estimado: 34.577 + 26.800 = ~61.400 chars** (−68%). Contrato de aceite: **≤ 90.000**.

---

## 7. Fases e paralelização

```
WAVE 0   F0(rede de segurança)                                  🔴 bloqueia tudo
             │
WAVE 1   F1 · F2 · F3 · F4 · F5 · F6 · F7 · F8                  🟢 blocos disjuntos do CLAUDE.md
             │  (barreira: só depois que TODOS os blocos migraram)
WAVE 2   F9(comprimir o que ficou + índice) → F10(política + guardas + back-refs)   🔴
             │
WAVE 3   F11(verificação final)                                 🔴
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | Rede de segurança (script + teste) | 🔴 | baixo | `docs_facts.py --check` roda verde contra o estado atual |
| 1 | **F1** | `docs/PLUGIN_BUS.md` (L782-926, L942-965) | 🟢 | baixo | −18,6k no CLAUDE.md, cobertura verde |
| 1 | **F2** | `docs/CANAIS_META.md` (L197-238) | 🟢 | **alto** | −13,4k, 5 tripwires presentes |
| 1 | **F3** | `docs/CANAIS.md` (L165-196, L239-284) | 🟢 | médio | −22,1k |
| 1 | **F4** | `docs/IA.md` (L285-295, L310-316, L380-442) | 🟢 | **alto** | −25,7k |
| 1 | **F5** | `docs/UI_CONVERSA.md` (L317-379) | 🟢 | médio | −20,3k |
| 1 | **F6** | `docs/PLUGINS.md` (L612-682, L743-781) | 🟢 | **alto** | −25,2k |
| 1 | **F7** | `docs/API_REST.md` + `docs/OPERACAO.md` + `DEPLOY_COOLIFY.md` | 🟢 | médio | −6,2k + gotchas longos migrados |
| 1 | **F8** | Dedupe nos guias existentes (auditoria, versionamento, modelo de dados) | 🟢 | baixo | −11,7k |
| 2 | **F9** | Comprimir o bloco que fica + criar o Índice de docs | 🔴 | médio | `wc` ≤ 90k [depende de: F1–F8] |
| 2 | **F10** | Política "onde documentar" + guarda de tamanho + back-refs | 🔴 | baixo | teste falha se CLAUDE.md > 90k |
| 3 | **F11** | Verificação final | 🔴 | baixo | sessão nova sem o aviso; suíte verde |

⚠️ **Sobre paralelizar a Wave 1:** as 8 fases escrevem em **blocos de linha disjuntos** do `CLAUDE.md`, mas no MESMO arquivo. Ou se despacha uma por vez (recomendado — cada fase = 1 commit, `git diff` legível), ou em worktrees separados com merge por bloco. **Nunca** dois editores simultâneos no mesmo checkout: o `CLAUDE.md` não tem marcadores de seção que sobrevivam a um merge textual.

⚠️ **A ordem F1→F8 é irrelevante, mas F9 é barreira real.** Comprimir antes de extrair faz o executor comprimir texto que ia migrar de qualquer jeito — trabalho jogado fora e risco dobrado de perder nuance.

---

### Fase 0 — Rede de segurança (antes de tocar em uma vírgula)

**Objetivo:** tornar "não perdi informação" **verificável por máquina**, em vez de uma promessa.

**Itens**
1. `[sequencial]` Criar `scripts/docs_facts.py` com dois modos:
   - `--snapshot [ref]` — extrai de `git show <ref>:CLAUDE.md` (default `HEAD`) o inventário de **fatos atômicos** e grava `docs/.facts.json`:
     - todo token entre crases (**1.751 únicos** hoje) — identificadores, chaves de config, nomes de evento/filtro, endpoints;
     - todo caminho de arquivo citado (**148 únicos**);
     - toda linha com `⚠️` ou `🚫` (**36**), pelo texto completo;
     - a primeira célula de cada linha de tabela (**195 linhas**).
   - `--check` — verifica que **cada fato** aparece em `CLAUDE.md` ∪ `docs/**/*.md`. Falha listando o que sumiu.
2. `[paralelo]` Criar `tests/contracts/test_docs_hygiene.py` com dois testes:
   - `test_claude_md_facts_preserved` — chama o `--check`;
   - `test_claude_md_size` — teto de tamanho, **começando em 190.000** (folga) e apertado para **90.000** só na F10. Mensagem de falha ensina o fluxo (§8.1).
3. `[paralelo]` Rodar `--snapshot` e commitar `docs/.facts.json` como golden.
4. `[sequencial]` Anotar os **15 caminhos que não resolvem** (§2.6) numa allowlist com comentário — caminhos do repositório de plugins são legítimos; `tests/integration/test_channel_credential_pattern.py` é referência morta e vira item da F3.

⚠️ O `--check` compara **presença de token**, não prosa. Ele prova que nada *sumiu*; não prova que o texto continua bom. A revisão humana da §11 continua obrigatória.

#### Status de execução — Fase 0
**Estado:** ✅ Concluída
- **O que foi feito:** `scripts/docs_facts.py` (snapshot/check/audit-paths), `docs/.facts.json` (golden de `921da9f`, 2.078 fatos: 1.751 tokens em crase, 153 caminhos, 35 tripwires, 139 chaves de tabela) e `tests/contracts/test_docs_hygiene.py` (2 testes).
- **Como foi feito / decisões:** o teto começou em 190.000 e foi apertado para 90.000 só na F10, para a suíte ficar verde durante toda a migração. A chave de tabela é comparada **sem markup** nos dois lados (o haystack é normalizado) — sem isso, célula como `` `ai_agents` / `ai_variables` `` nunca casava e o check dava 14 falsos negativos.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `--check` verde contra o estado pré-corte; `pytest tests/contracts/test_docs_hygiene.py` verde.

---

### Fases 1–8 — Extração (Wave 1)

**Objetivo comum:** mover o texto integral para o guia e deixar no `CLAUDE.md` o bloco no formato de §4.

**Receita idêntica para cada fase** (o executor repete literalmente):
1. `[sequencial]` Criar o guia em `docs/` com o cabeçalho padrão e **colar o texto atual verbatim**, agrupado por assunto/plano. Nesta etapa **não reescreva nada** — copiar primeiro, editar depois é o que mantém a prova de cobertura verde.
2. `[sequencial]` Substituir o bloco no `CLAUDE.md` pelo resumo: regra em imperativo + **todos** os `⚠️`/`🚫` daquele bloco como uma linha cada + link para o guia.
3. `[sequencial]` `python3 scripts/docs_facts.py --check` → verde.
4. `[paralelo]` Ajustar os back-references que apontavam para a seção movida (§9.2).
5. Commit único: `docs(plano 139): <área> migra para docs/<GUIA>.md`.

**Escopo por fase**

| Fase | Blocos do `CLAUDE.md` | Guia | Tripwires que **ficam** no CLAUDE.md |
|---|---|---|---|
| F1 | L782-926, L942-965 | `docs/PLUGIN_BUS.md` | L813 (`channel_id`/`conversation_id` no bus), L856 + L962 (🚫 media type novo não existe) |
| F2 | L197-238 | `docs/CANAIS_META.md` | L204 (Instagram diverge por flag), L215 (`human_window_hours` é property), L217 (quem garante humano não é o `send_text`), L219 (são TRÊS janelas), L221 (bloquear o chat exigiu core), L237 (App Secret em canal legado) |
| F3 | L165-196, L239-284 | `docs/CANAIS.md` | L171 (DOIS defaults de JID + a tabela inteira), L262 (autofill do navegador em campo `secret`) |
| F4 | L285-295, L310-316, L380-442 | `docs/IA.md` | L292 (direções de mídia / `_NO_SEED`), L426 (todo caminho de atribuição cala), L433 (`transfer_to_human` fecha o gate no turno; **época antes do gate**) |
| F5 | L317-379 | `docs/UI_CONVERSA.md` | L334, L345, L346, L352 (`setPendingAudio` substitui a fila), L355 (bolha otimista adota o `msg_id` do ACK), L361, L362, L364, L366 (âncora não anexa `new_message`) |
| F6 | L612-682, L743-781 | `docs/PLUGINS.md` | L626 ("não muda o core" ≠ "não depende do core"), L652 (largura do modal), L654 (`custom_sounds`/`notifications` não são plugins), L665 (🚫 `new WebSocket('/ws')`), L689 (`entry.services` nunca por HTTP), L770 |
| F7 | L456-506, L1076-1083 | `docs/API_REST.md`, `docs/OPERACAO.md`, `docs/DEPLOY_COOLIFY.md` | L1082 (IP autodeclarado é forjável — rate-limit **nunca** usa `audit_ip`), gotcha do echo, gotcha do `statics/` |
| F8 | L697-742, células longas de L103-124 | guias existentes | L701 (a constante ficou congelada 93 dias), L925 (teste do core nunca fixa `assets/plugin_examples/<id>` ≠ `gowa`) |

**Pronto quando (cada fase):** `docs_facts.py --check` verde; o guia abre e é navegável; o bloco residual no `CLAUDE.md` cabe no orçamento da §6; `git diff --stat` mostra o `CLAUDE.md` encolhendo pelo valor previsto (±20%).

#### Status de execução — Fases 1–8
**Estado:** ✅ Concluída
- **O que foi feito:** 10 guias novos — `PLUGIN_BUS.md` (21,7k), `PLUGINS.md` (31,0k), `CANAIS.md` (24,6k), `CANAIS_META.md` (16,0k), `IA.md` (21,7k), `UI_CONVERSA.md` (20,8k), `OPERACAO.md` (14,1k), `API_REST.md` (6,7k), `TESTES.md` (4,7k), `FRONTEND.md` (2,8k) — e 4 existentes ampliados por apêndice: `PLUGINS_AUDITAVEIS.md`, `PLUGIN_API_CHANGELOG.md`, `analises/01-modelo-de-dados.md`, `DEPLOY_COOLIFY.md`.
- **Como foi feito / decisões:** extração **programática** por faixa de linhas (script de build), não transcrição — cada bloco foi copiado VERBATIM, sem reescrita. Isso é o que faz o `--check` ser prova de verdade. **Desvio do plano:** o gotcha de `statics/` ficou íntegro em `OPERACAO.md` e o `DEPLOY_COOLIFY.md` recebeu um ponteiro, em vez de a íntegra ser duplicada nos dois.
- **Problemas / pendências:** o `--check` pegou 3 fatos perdidos na 1ª montagem — as faixas L570-581 (intro do sistema de plugins) e L966-969 ("Criar um plugin novo") não tinham destino. Corrigido ampliando as faixas de `docs/PLUGINS.md`. **Foi exatamente o modo de falha que a Fase 0 existe para pegar.**
- **Verificação:** `docs_facts.py --check` verde após a correção; particionamento das 1.089 linhas conferido (nenhuma faixa órfã).

---

### Fase 9 — Comprimir o que ficou + Índice de docs 🔴

**Objetivo:** aplicar a regra dos três pedaços (§2.3) ao bloco que **não** migrou, e dar ao `CLAUDE.md` um índice que diga **quando** abrir cada guia.

**Itens**
1. `[sequencial]` Aplicar a regra (a)(b)(c) às linhas-ensaio remanescentes — sobretudo os 4 mega-bullets de `## Gotchas` e a `### Tabelas` do banco (a célula de `messages` tem 939 chars sozinha).
2. `[sequencial]` Comprimir Stack, Comandos, Testes, Tema/modo escuro, Convenções obrigatórias e "Onde vive o código de um plugin" — **preservando integralmente as tabelas** de Comandos e dos 4 lugares onde vive o código de um plugin (são desambiguação, não prosa).
3. `[sequencial]` Criar, logo depois do `## Arquitetura`, a seção **`## Índice de documentação — leia ANTES de mexer`**:

   | Vai mexer em… | Leia antes |
   |---|---|
   | evento/filtro de plugin, payload do bus | `docs/PLUGIN_BUS.md` + `plugins/events.py` |
   | provider de canal, descriptor, dedup, proxy | `docs/CANAIS.md` |
   | Messenger/Instagram, janela de 24h/7d | `docs/CANAIS_META.md` |
   | motor de IA, gate humano, roteamento | `docs/IA.md` |
   | compositor, thread, sidebar | `docs/UI_CONVERSA.md` |
   | sistema de plugins, RBAC, services | `docs/PLUGINS.md` |
   | deploy, persistência, IP/proxy | `docs/DEPLOY_COOLIFY.md`, `docs/OPERACAO.md` |

   ⚠️ O verbo é **imperativo** ("leia antes"), não convite. É o único mecanismo de descoberta que substitui a leitura obrigatória de hoje.

**Pronto quando:** `python3 -c "print(len(open('CLAUDE.md',encoding='utf-8').read()))"` ≤ 90.000 e `--check` verde.

#### Status de execução — Fase 9
**Estado:** ✅ Concluída
- **O que foi feito:** `CLAUDE.md` remontado a partir de 20 faixas verbatim + 15 blocos-resumo autorais, com a nova seção `## Índice de documentação — leia ANTES de mexer` logo após o cabeçalho.
- **Como foi feito / decisões:** o que é **desambiguação** foi preservado verbatim, não resumido — tabela dos DOIS defaults de JID, tabela dos 4 lugares onde vive o código de um plugin, tabela de launchers, `## Arquitetura` inteira, `## Convenções de código`, `### Convenções obrigatórias`, `## GOWA REST API` e os 14 gotchas curtos. Os tripwires SUBIRAM de 35 para **45** linhas: vários avisos que estavam diluídos em prosa viraram `⚠️` explícito.
- **Problemas / pendências:** nenhuma. Duas correções cosméticas pós-montagem (lista solta e a concordância "Guia de o" → "Guia do" no cabeçalho dos guias).
- **Verificação:** 66.963 chars, 561 linhas, 5 pares de fence balanceados, zero heading duplicado, `--check` verde.

---

### Fase 10 — Política + guardas + back-references 🔴

**Objetivo:** impedir a recaída. Sem esta fase o plano compra ~5 semanas (§2.1).

**Itens**
1. `[sequencial]` Apertar `test_claude_md_size` para **90.000** e deixar a mensagem de falha ensinando o fluxo:
   `"CLAUDE.md passou de 90k. NÃO aumente o teto: mova a narrativa para o guia de docs/ correspondente e deixe aqui a regra + o ⚠️ + o link."`
2. `[sequencial]` Acrescentar ao `CLAUDE.md` (dentro de `## Convenções de código`, ~600 chars) a política:
   - **onde escrever**: regra dura + tripwire → `CLAUDE.md`; mecanismo, história, medição, números de produção → o guia temático em `docs/`;
   - **orçamento**: um plano executado pode acrescentar **até ~2 linhas** ao `CLAUDE.md`; o resto vai para o guia;
   - `docs-planos/` **não** é destino de documentação durável (é podado).
3. `[paralelo]` Atualizar o `.claude/commands/plan.md` (Passo 0.1) — hoje manda "Leia o `CLAUDE.md` da raiz"; passa a mandar ler **o `CLAUDE.md` + os guias de `docs/` da área afetada**, e a **escrever a documentação do plano no guia**, não no `CLAUDE.md`.
4. `[paralelo]` Atualizar os back-references por nome de seção (§9.2).
5. `[paralelo]` Atualizar `README.md:173` ("Documentação técnica completa em CLAUDE.md") para apontar ao índice de `docs/`.

#### Status de execução — Fase 10
**Estado:** ✅ Concluída
- **O que foi feito:** teto apertado para `LIMITE = 90_000`; política de documentação acrescentada como bullet em `## Convenções de código`; **18 back-references** atualizados em 9 arquivos (`.claude/commands/new-plugin.md` ×7, `new-channel.md` ×2, `plan.md`, `plugins/semver.py`, `plugins/services.py`, `server/routes/contacts.py`, `README.md`, 3 arquivos de caracterização).
- **Como foi feito / decisões:** o `.claude/commands/plan.md` passou a mandar ler os guias da área **e** a escrever a documentação do plano no guia, com o orçamento de ~2 linhas no `CLAUDE.md` — é a costura que fecha o ciclo, já que era o `/plan` que alimentava o crescimento.
- **Problemas / pendências:** nenhuma; todos os 18 trechos casaram exatamente (nenhum "NÃO ENCONTRADO").
- **Verificação:** `py_compile` nos 8 arquivos `.py` tocados; `pytest tests/contracts/test_docs_hygiene.py` verde com o teto em 90k.

---

### Fase 11 — Verificação final 🔴

**Itens**
1. `[sequencial]` `venv/bin/python -m pytest tests/contracts` verde (inclui as duas guardas novas).
2. `[sequencial]` `venv/bin/python -m pytest` (suíte inteira, `WHATSBOT_TEST_DB_URL` apontando a um banco `*test*` UTF-8) — comparar com a linha de base de falhas pré-existentes conhecidas (alembic ×2, matriz de auditoria, suíte legada dependente do `protocolos` instalado).
3. `[sequencial]` Abrir uma **sessão nova** do Claude Code no repositório e confirmar que o aviso `⚠ CLAUDE.md is over the 150.0k-char limit` **não aparece**.
4. `[sequencial]` Teste de utilidade (o que o `--check` não cobre): fazer uma pergunta de cada área a uma sessão nova — *"por que `human_window_hours` é property?"*, *"o que acontece se um filtro `filter.webhook.payload` devolver `None`?"*, *"posso registrar um media type novo?"* — e confirmar que a sessão chega à resposta certa (direto do `CLAUDE.md` ou abrindo o guia). Se falhar, o resumo daquele bloco está curto demais.

#### Status de execução — Fase 11
**Estado:** ✅ Concluída
- **O que foi feito:** suíte `tests/contracts` verde (149 passed, 1 skipped); suíte completa executada; teste de utilidade ampliado de 3 para **18 regras-chave**, todas encontráveis no próprio `CLAUDE.md`.
- **Como foi feito / decisões:** o `--audit-paths` (bônus da Fase 0) confirmou a P5 e achou 15 caminhos que não resolvem — 14 são falsos positivos conhecidos (repositório irmão de plugins e truncamento de regex), 1 é referência morta real, corrigida em `docs/CANAIS.md`.
- **Problemas / pendências:** cobertura de teste do plano 104 F3 (formato de credencial) segue **em aberto** — descoberta, não causada, por este plano.
- **Verificação:** ver o Checklist §12.
- **Suíte completa — leitura do resultado (importante para quem repetir):** a 1ª rodada no `whatsbot_test` compartilhado voltou com **20 failed + 14 errors**. Não era regressão: outra sessão rodava `pytest` no MESMO banco durante a janela (12:17–12:36), e `tests/pg.py` faz `DROP SCHEMA public CASCADE` **uma vez por processo** — cada rodada apagando o schema da outra. Diagnóstico em três passos: (1) `git diff -U0 -- '*.py'` mostra **8 linhas alteradas em 6 arquivos, todas comentário/docstring** — nenhuma linha executável, logo o diff não pode quebrar teste de integração; (2) `test_schema_drift` **passa sozinho** e falhava no lote; (3) a mesma seleção num **banco dedicado** (`whatsbot_test_p139`, `ENCODING 'UTF8' TEMPLATE template0`) e sem concorrente caiu para **2 falhas** — `test_audit_matrix_is_complete` (pré-existente conhecida) e `test_audio_transcription_lands_on_audio_row`, que **passa sozinho** (6 passed) e é poluição de ordem dentro do lote. Linha de base pré-existente do repo: alembic ×2, matriz de auditoria e suíte legada.
- **Nota de higiene:** `tests/contracts/test_docs_hygiene.py` carrega o `docs_facts` por **caminho** (`importlib.util.spec_from_file_location`), não com `sys.path.insert(0, "scripts")` — um insert ali valeria para a sessão inteira do pytest e deixaria `scripts/` na frente do projeto para todo import subsequente.

---

## 8. As duas guardas

### 8.1 Teto de tamanho

```python
# tests/contracts/test_docs_hygiene.py
LIMITE = 90_000   # teto do projeto; o do Claude Code é 150k
def test_claude_md_size():
    n = len(Path("CLAUDE.md").read_text(encoding="utf-8"))
    assert n <= LIMITE, (
        f"CLAUDE.md tem {n} chars (teto {LIMITE}). NÃO aumente o teto: "
        "mova a narrativa para o guia de docs/ e deixe aqui regra + ⚠️ + link."
    )
```

⚠️ **O teto é 90k, não 150k, de propósito.** Deixar a guarda no limite do Claude Code faria o teste só acusar quando já é tarde — e daria a impressão de que 149k está "ok", quando 149k já custa ~47k tokens em todo turno.

### 8.2 Prova de cobertura

`scripts/docs_facts.py --check` compara o inventário congelado em `docs/.facts.json` (tirado do `CLAUDE.md` **antes** do corte) com a união `CLAUDE.md` ∪ `docs/**/*.md`. É o que transforma D1 em algo checável, e o que permite executar as fases em paralelo com confiança.

Bônus: como o script já varre caminhos citados, ele passa a acusar referência morta como a de §2.6.

---

## 9. Anexos operacionais

### 9.1 Os 36 tripwires (nenhum sai do `CLAUDE.md`)

L171, L202, L204, L215, L217, L219, L221, L231, L237, L262, L292, L334, L345, L346, L352, L355, L361, L362, L364, L366, L426, L433, L626, L652, L654, L665, L689, L701, L770, L813, L856, L925, L940, L962, L1082 — mais o 🚫 de `filter.media.unknown` (L856/L962, contado uma vez).

### 9.2 Back-references a atualizar

| Arquivo | Referência | Ação |
|---|---|---|
| `.claude/commands/new-plugin.md:18,19,341,395` | "a tabela do `CLAUDE.md`" (eventos/filtros) | → `docs/PLUGIN_BUS.md` |
| `.claude/commands/new-plugin.md:138` | §"API interna plugin→plugin" | → `docs/PLUGINS.md` |
| `.claude/commands/new-plugin.md:455` | §"Tema e modo escuro" | → `docs/FRONTEND.md` |
| `.claude/commands/new-channel.md:39,195` | §"Contrato de identidade" e §"Provider de canal" | → `docs/CANAIS.md` |
| `.claude/commands/plan.md:9` | "Leia o `CLAUDE.md` da raiz" | + guias de `docs/` da área (F10.3) |
| `plugins/semver.py:30` | §"Versionamento da API de plugins" | → `docs/PLUGIN_API_CHANGELOG.md` |
| `plugins/services.py:35` | §"Auditoria de plugins" | → `docs/PLUGINS_AUDITAVEIS.md` |
| `server/routes/contacts.py:1490` | gotcha "Echo do próprio envio" | → `docs/OPERACAO.md` |
| `tests/core/characterization/test_agno_reply_extraction.py:7,18` | "CLAUDE.md flags this…" | → `docs/IA.md` |
| `tests/integration/characterization/test_killswitch_characterization.py:38,275` | "CLAUDE.md and the Plano-23 brief" | → `docs/PLUGINS.md` |
| `tests/integration/characterization/test_webhook_characterization.py:201` | "CLAUDE.md-documented `allowed_jid_types`" | → `docs/CANAIS.md` |
| `README.md:173` | "Documentação técnica completa em CLAUDE.md" | → índice de `docs/` |

⚠️ Comentário em teste que cita uma seção movida **não quebra a suíte** — some em silêncio. Trate a §9.2 como checklist manual da F10, não como algo que o vermelho vai lembrar.

---

## 10. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Resumo curto demais | Uma sessão futura lê a regra, não abre o guia e reintroduz um bug que a história prevenia | D4 (todo ⚠️ fica) + Índice imperativo (F9.3) + teste de utilidade (F11.4) |
| Compressão perde nuance | O executor "resume" e mata a condição exata (ex.: "época **antes** do gate") | Fases 1–8 **copiam verbatim** primeiro; só a F9 comprime, e só o que ficou |
| Guia vira lixão | 10 arquivos novos que ninguém abre | O índice diz **quando**; cada guia abre com o escopo; back-refs (§9.2) apontam do código para o guia certo |
| Recaída em ~5 semanas | O arquivo volta a 150k | F10 (política + teto de 90k). **Sem a F10 o plano é paliativo** |
| Merge/paralelismo | Duas fases editando o mesmo `CLAUDE.md` | Um escritor por vez, ou worktrees (§7) |
| Perda silenciosa | Um parágrafo cai no meio de um recorte | `docs_facts.py --check` em toda fase |
| `@import` | Alguém "otimiza" trocando link por `@` | D3 documentado na política da F10 |
| `.facts.json` desatualizado | Vira golden que ninguém regenera e o `--check` passa a mentir | Regenerar **só** com `--snapshot` explícito e justificativa no commit; nunca no fluxo normal |

---

## 11. Perguntas em aberto

**P1 — Qual o alvo de tamanho?**
Contexto: a estimativa seção a seção dá ~61k; o limite do Claude Code é 150k; a guarda proposta é 90k.
(a) **90k** — recomendado: cabe folgado, deixa o executor manter mais texto onde tiver dúvida, e ainda corta o custo de contexto em >50%.
(b) 61k — o alvo da estimativa; mais enxuto, mais risco de resumo curto demais.
(c) 120k — conservador; volta ao limite em ~2 semanas de ritmo normal.
→ ✅ **DECIDIDO (2026-08-24): (a) 90k.** O resultado real ficou em **66.963**, com 23k de folga sob o teto.

**P2 — Executar em uma tranche ou em duas?**
(a) **Tranche única** (F0→F11) — recomendado: o `--check` só é confiável quando todas as extrações fecharam, e um `CLAUDE.md` meio-migrado é o pior dos dois mundos.
(b) Duas tranches (Wave 1 agora; F9/F10 depois) — o arquivo fica ~80k já na primeira, mas a política demora.
→ ✅ **DECIDIDO (2026-08-24): (a) tranche única** — F0→F11 numa sessão.

**P3 — `docs/` ou `.claude/skills/`?**
Contexto: skills carregam sob demanda por descrição, o que é descoberta mais forte que um link.
(a) **`docs/` agora** — recomendado: é a convenção do repositório, os guias servem humanos também, e 3 guias já funcionam assim.
(b) Skills agora — muda dois mecanismos ao mesmo tempo (organização + descoberta).
→ ✅ **DECIDIDO (2026-08-24): (a) `docs/`.** 10 guias novos + 4 existentes ampliados. Skills seguem como plano futuro.

**P4 — A tabela de `## Gotchas` some ou vira índice?**
(a) **Vira lista de uma linha por gotcha, com link quando houver guia** — recomendado.
(b) Migra inteira para `docs/OPERACAO.md` deixando só o link — arrisca perder o "esbarrão" fortuito, que é o valor dos gotchas.
→ ✅ **DECIDIDO (2026-08-24): (a).** `## Gotchas` ficou com 21 bullets curtos (14 verbatim) e 5 condensados com link; a íntegra está em `docs/OPERACAO.md`.

**P5 — `tests/integration/test_channel_credential_pattern.py` não existe (§2.6).** O teste foi renomeado, absorvido ou nunca criado? Descobrir na F3 e corrigir a citação (ou o plano 104 tem uma pendência real de cobertura).
→ ✅ **CONFIRMADO (2026-08-24): o teste NUNCA existiu.** Não há nada em `tests/` citando `credential_format_errors` nem `pattern_error`, e `constants.test.js` não exercita `validateCredentials`. **As duas metades do plano 104 F3 estão sem rede** — a afirmação "travado por…" era falsa nos dois lados. A correção foi escrita em `docs/CANAIS.md` e a cobertura fica como pendência aberta (fora do escopo deste plano).

---

## 12. Checklist de verificação

- [x] `python3 scripts/docs_facts.py --check` verde após **cada** fase (não só no fim)
- [x] `len(CLAUDE.md)` ≤ 90.000 chars — **66.963**
- [x] Tripwires presentes no `CLAUDE.md`: **45** linhas (subiu de 35 — avisos antes diluídos em prosa viraram `⚠️` explícito)
- [x] Nenhum `@import` introduzido
- [x] `## Arquitetura` intacta (copiada verbatim)
- [x] Tabelas de desambiguação preservadas: DOIS defaults de JID, 4 lugares onde vive o código de um plugin, launchers por ambiente, MAJOR/MINOR/PATCH da API
- [x] Todo guia novo de `docs/` abre com escopo e está linkado do Índice
- [x] Back-references da §9.2 atualizados — 18 trechos em 9 arquivos
- [x] `venv/bin/python -m pytest tests/contracts` verde (149 passed, 1 skipped)
- [x] Suíte completa no Postgres sem regressão sobre a linha de base conhecida — as 20 falhas/14 erros da 1ª rodada foram **contenção de banco com outra sessão**, não o diff (ver Fase 11)
- [x] `CLAUDE.md` em 66.963 chars — bem abaixo do teto de 150k do Claude Code (confirmar visualmente na próxima sessão)
- [x] Teste de utilidade da F11.4 ampliado: **18 regras-chave**, todas encontráveis no `CLAUDE.md`
- [x] `.claude/commands/plan.md` manda escrever no guia, não no `CLAUDE.md`

---

## 13. Apêndice — arquivos que o executor toca

**Documentação (núcleo da entrega)**
`CLAUDE.md` · `README.md` · `docs/PLUGIN_BUS.md`* · `docs/PLUGINS.md`* · `docs/CANAIS.md`* · `docs/CANAIS_META.md`* · `docs/IA.md`* · `docs/UI_CONVERSA.md`* · `docs/API_REST.md`* · `docs/OPERACAO.md`* · `docs/TESTES.md`* · `docs/FRONTEND.md`* · `docs/DEPLOY_COOLIFY.md` · `docs/PLUGINS_AUDITAVEIS.md` · `docs/PLUGIN_API_CHANGELOG.md` · `docs/analises/01-modelo-de-dados.md` (`*` = novo)

**Guardas**
`scripts/docs_facts.py`* · `docs/.facts.json`* · `tests/contracts/test_docs_hygiene.py`*

**Back-references (comentário/prosa apenas — nenhuma mudança de comportamento)**
`.claude/commands/new-plugin.md` · `.claude/commands/new-channel.md` · `.claude/commands/plan.md` · `plugins/semver.py` · `plugins/services.py` · `server/routes/contacts.py` · `tests/core/characterization/test_agno_reply_extraction.py` · `tests/integration/characterization/test_killswitch_characterization.py` · `tests/integration/characterization/test_webhook_characterization.py`
