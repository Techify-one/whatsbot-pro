# Plano 78 — Limpar vazamentos antes de abrir os repositórios (00 · mestre)

> **Status:** ✅ EXECUTADO (repo `whatsbot-pro`) em 2026-07-23 · **Escopo:** grande (reescrita de histórico git em 4 branches do `whatsbot-pro`; `whatsbot-pro-plugins` NÃO tocado — ver P1)
>
> **⚠️ EXECUÇÃO (2026-07-23):** O sub-plano **01 foi executado por completo** (Fases A→F) e o histórico reescrito foi **force-pushed** para `origin` nos **4** branches (`main`, `developer`, `feat/plano-65-envio-video`, `plano-76-desacoplar-providers` — este 4º não estava no plano, apareceu depois). O sub-plano **02 NÃO foi executado** (P1 = só o core público). O **escopo real cresceu** além deste plano (achados no baseline): +2 IPs internos (`10.8.200.104`, `10.8.200.13`), +3 hostnames com nome de pessoa (`whatsbot-{ezequiel,luisa,thiago}`), genericização COMPLETA do nome do cliente ("Redes Brasil"/`redes.cc`/`redesbrasil.net`/`RedesBrasilDB_owner`/`RedesBrasil_bot`/`RBNexusDB`), tokens do plugin `utm_atendente` (`MTCSE`/`ia-anna`), +3 telefones reais, e-mails externos de PII, e **anonimização de autoria via `--mailmap`**. Verificação: re-clone limpo do origin = **0 hits** em ~20 famílias de token, 0 e-mail pessoal na autoria, doc 74 sumiu; gitleaks = 3 achados **todos falsos-positivos** (fixtures fake de teste). **Pendências** (não feitas, ver sub-plano 01 Fase F): reconciliar o checkout local (WIP preservado, anchor `backup/developer-pre-plano78`); as **branches locais nunca-enviadas ainda carregam os vazamentos** (não pushar); avisar colaboradores a re-clonar.
> **Origem:** auditoria de segurança/privacidade feita nesta sessão (gitleaks em todo o histórico + zips extraídos, grep dirigido por padrões conhecidos, e um workflow com 38 sub-agentes lendo ~230 arquivos — todo `docs-planos/`+`docs/analises/` histórico incluindo versões já deletadas, `tests/`, `assets/plugin_examples/`, as 2 PRs, e o repo `whatsbot-pro-plugins` inteiro — com verificação adversarial item a item) antes de tornar `Techify-one/whatsbot-pro` e `Techify-one/whatsbot-pro-plugins` públicos.
> **Método:** tudo verificado por leitura direta do arquivo real (`arquivo:linha`) ou por `git show <ref>:<path>` quando o arquivo já foi apagado do working tree mas continua no histórico. Nenhuma citação é de memória.
> Nenhuma senha, API key ou chave privada vazou em nenhum dos dois repositórios (gitleaks limpo). O que vazou é **infraestrutura interna real** (IPs, hostnames, nomes de serviço, um caminho de escalação de privilégio documentado), **PII de terceiros** (nome+e-mail de funcionário, nome+endereço+contrato de dois clientes finais), e **conteúdo de negócio específico de um cliente** (o plugin `vendas_ia`, cuja própria descrição do projeto já dizia que "não deveria estar no git").
>
> **Como usar este plano**: leia este mestre inteiro primeiro (principalmente §5 Metodologia e §7 Perguntas em aberto — há decisões que só o dono do produto pode tomar). Depois execute os sub-planos na ordem do §4. Ao executar cada fase de qualquer sub-plano, preencha o "Status de execução" dela ANTES de passar para a próxima.

---

## 1. Sub-planos deste plano

| Arquivo | Repositório | Cobre |
|---|---|---|
| [78-plano-limpeza-vazamentos-pre-opensource-01-whatsbot-pro.md](78-plano-limpeza-vazamentos-pre-opensource-01-whatsbot-pro.md) | `Techify-one/whatsbot-pro` (este checkout) | Redação/remoção de conteúdo no working tree + reescrita do histórico em `main`/`developer`/`feat/plano-65-envio-video` |
| [78-plano-limpeza-vazamentos-pre-opensource-02-whatsbot-pro-plugins.md](78-plano-limpeza-vazamentos-pre-opensource-02-whatsbot-pro-plugins.md) | `Techify-one/whatsbot-pro-plugins` | Decisão de escopo sobre `vendas_ia`/`guarda_ia` + redação + reescrita do histórico em `master` |

Os dois repositórios são **independentes** (remotes, checkouts e branches diferentes) — os dois sub-planos podem ser executados **em paralelo** por sessões/pessoas diferentes. A única dependência real é que **nenhum dos dois vá para o ar (visibilidade pública) antes de terminar sua própria limpeza** — não há necessidade de sincronizar a ordem entre eles.

---

## 2. Decisões do usuário / travadas (não reabrir)

Nenhuma decisão foi travada ainda nesta sessão — as decisões que impactam o *escopo* deste plano estão em **§7 Perguntas em aberto** e precisam de uma resposta do dono do produto antes (ou durante) a execução. Um princípio que já vale para as duas frentes:

| # | Princípio | Consequência no plano |
|---|---|---|
| **P** | Este repo **já é usado como referência de arquitetura/processo** (os próprios `docs-planos/` são um diferencial do produto: mostram disciplina de engenharia). A limpeza deve ser **cirúrgica** (remover/redigir só o que vaza infra/PII real), não um "zerar tudo por segurança" | Nenhum sub-plano propõe apagar `docs-planos/`/`docs/analises/` inteiros — só os itens específicos listados em §6 |

---

## 3. Resumo executivo

Os dois repositórios (`whatsbot-pro`, privado, e `whatsbot-pro-plugins`, privado) já têm todo o conteúdo sensível **commitado e enviado para o GitHub** (branches `main`/`developer`/`feat/plano-65-envio-video` em `whatsbot-pro`; `master` em `whatsbot-pro-plugins`). Simplesmente apagar um arquivo do working tree — como já foi feito, sem commitar, para 6 arquivos de `docs-planos/` — **não remove nada do histórico**: qualquer um com acesso ao repo (e todo mundo, no dia em que ele virar público) continua vendo o conteúdo antigo via `git log -p` ou `git show <commit antigo>`.

A limpeza tem duas camadas obrigatórias e **na ordem certa**:

1. **Conteúdo** — decidir o texto final de cada arquivo afetado (redigir strings sensíveis, ou remover o arquivo inteiro) e commitar essa versão limpa.
2. **Histórico** — usar `git-filter-repo` (já instalado neste servidor, `v2.47.0`, `/home/thiago/.local/bin/git-filter-repo`) para reescrever **todos os commits antigos**, removendo os arquivos condenados e substituindo as strings sensíveis em todo blob que já existiu — não só no HEAD atual.

Sem o passo 2, o passo 1 é decoração: o dado sensível continua público no histórico.

---

## 4. Ordem de operações

```
WAVE 0  01-whatsbot-pro (Fases A→F) ─┬─ 02-whatsbot-pro-plugins (Fases A→E)   ← independentes, pode rodar em paralelo
                                      │
WAVE 1  Confirmar com o time que ninguém mais tem um clone/fork antigo desses repos
                                      │
WAVE 2  Só então: alternar visibilidade para Public no GitHub (ação manual, fora deste plano)
```

Dentro de cada sub-plano as fases são majoritariamente **sequenciais** (redigir conteúdo → clonar limpo → reescrever histórico → verificar → force-push) porque cada uma depende do resultado da anterior; a paralelização real está **entre os dois sub-planos**, não dentro deles.

---

## 5. Metodologia compartilhada (`git-filter-repo`)

Ambos os sub-planos usam a mesma receita. Documentada aqui uma vez para não duplicar.

### 5.1 Duas técnicas, um comando

`git-filter-repo` aplica dois tipos de filtro, e os dois entram na **mesma invocação**:

| Técnica | Flag | Uso neste plano |
|---|---|---|
| Remoção de arquivo inteiro (todas as versões, todos os commits) | `--path <arquivo> --invert-paths` (repetir `--path` para cada arquivo a remover) | `docs-planos/74-...md`, e (se a decisão do §7 P2 for "remover") `plugins/vendas_ia/`, `plugins/guarda_ia/` |
| Substituição de texto (toda ocorrência, em todo blob, todo commit) | `--replace-text <arquivo-de-regras>` | IPs internos, e-mail/nome de funcionário, nome+endereço de clientes, número de telefone de teste, hostname com nome de dev |

`--replace-text` recebe um arquivo com uma regra por linha, formato `string_antiga==>string_nova` (ou regex com prefixo `regex:`). Cada sub-plano lista as regras exatas na sua própria Fase de reescrita.

⚠️ **A confirmar durante a execução:** não há certeza de que `--path ... --invert-paths` e `--replace-text` combinam sem atrito na mesma chamada (não testado neste ambiente). Se o `git-filter-repo` recusar a combinação, rode em **duas passagens sequenciais** na mesma cópia: primeiro a remoção de caminhos, depois o `--replace-text` com a flag extra `--force` (exigida porque o filter-repo recusa rodar de novo num repo que ele já processou — isso é uma trava de segurança proposital, não um bug).

### 5.2 Passo a passo (por repositório)

1. **Clone dedicado e limpo** — `git-filter-repo` se recusa a rodar num checkout com remotes configurados normalmente ou mudanças não commitadas (proteção contra rodar sem querer no clone de trabalho do dia a dia). Sempre:
   ```
   git clone --no-local <url-do-remote> <pasta-temporaria-de-rewrite>
   cd <pasta-temporaria-de-rewrite>
   ```
   Isso traz **todos os branches remotos** automaticamente como locais (o filter-repo opera sobre tudo que existir no clone).
2. Rodar o(s) comando(s) de `git-filter-repo` (path removal + replace-text) — ver a Fase específica de cada sub-plano.
3. **Verificar** (§5.3) antes de tocar no remoto.
4. `git-filter-repo` remove o remote `origin` automaticamente ao final (outra trava de segurança — evita um push acidental de histórico reescrito). Readicionar deliberadamente:
   ```
   git remote add origin <url-do-remote>
   ```
5. **Force-push de todos os branches afetados** (não só o atual):
   ```
   git push origin --force --all
   git push origin --force --tags   # só se houver tags
   ```
6. Avisar qualquer pessoa com um clone existente desses repos: o histórico mudou, ela precisa **re-clonar** (não `git pull`) — um `pull` normal vai gerar conflitos/duplicatas confusas porque os hashes de commit mudaram.

### 5.3 Verificação pós-rewrite (obrigatória, nas duas frentes)

Depois do force-push, re-clonar **de novo, limpo**, e confirmar que sumiu:

```
git clone <url-do-remote> verify-clean && cd verify-clean
git log --all -p | grep -F -f /caminho/para/lista-de-strings-sensiveis.txt
# esperado: ZERO output
```

Rodar também o `gitleaks` usado nesta auditoria (`gitleaks detect --source . --log-opts="--all"`) — deve continuar limpo (já estava limpo antes; serve para garantir que a reescrita não introduziu nada novo por acidente).

### 5.4 Riscos gerais da reescrita de histórico

| Ponto | Risco | Mitigação |
|---|---|---|
| Tamanho do repo | `whatsbot-pro/.git` tem ~195 MB, `main`/`developer`/`feat/plano-65-envio-video` somam 383+752+713 commits — o filter-repo pode demorar alguns minutos e reempacotar o repo inteiro | Rodar num clone descartável, não no checkout de trabalho; ter paciência, não interromper no meio |
| Colaboradores existentes | Qualquer clone/fork já existente (de qualquer um dos 6 autores de commit, ou de CI/Coolify se algum deploy faz `git pull` direto desses branches) fica com histórico **divergente** do remoto reescrito | Confirmar no time quem tem clone local antes do force-push; depois do push, todo mundo re-clona |
| Perda de dados por erro no filtro | Uma regra de `--replace-text` mal escrita pode corromper texto não-relacionado (ex.: um regex genérico demais) | Cada regra listada nos sub-planos é uma string literal específica, não um regex genérico; revisar a lista antes de rodar |
| Branches locais não-`origin` | Este checkout (`whatsbot-pro`) tem branches **locais** que nunca foram para o GitHub (`plano-42`, `plano-69`, `plano-71-atendente-padrao`, `plano-72-vazamento-abas`, `merge-melhorias-extraction`, `backup/*`, `feature/aposentar-senha-painel-plano48`) — fora do escopo deste plano porque nunca ficaram públicas, mas **se algum dia forem enviadas ao GitHub, carregam os mesmos vazamentos** (foram cortadas da mesma história) | Não apagar essas branches locais sem confirmar com o usuário (podem ser trabalho em andamento) — só uma nota de atenção para o futuro, registrada aqui |
| Mudanças não-commitadas atuais | O working tree deste checkout já tem edições em progresso (`CLAUDE.md`, `channels.py`, `plugin.yaml`, `channel_webhook.py`, `useMediaUpload.js`, `mediaLimits.js`, `test_plano75_bus_events.py`) e arquivos novos não-relacionados a este plano (`76-plano-plugin-retornos-automaticos-reguas.md`, `77-plano-atributo-orfao-bloqueia-salvar-contato.md`, `melhorias-plugin-producao.zip`, `plano-importacao-historico-chatwoot/`) | Este plano **não mexe** nesses arquivos — a Fase A do sub-plano 01 só toca nos arquivos listados em §6. Não fazer `git add -A`/commit genérico durante a execução |

---

## 6. Inventário consolidado

Tabela-mestra de tudo que a auditoria confirmou. Severidade e ação detalhada em cada sub-plano; aqui só o mapa.

### 6.1 `whatsbot-pro` — ver sub-plano 01

| Severidade | Arquivo | O quê |
|---|---|---|
| 🔴 Crítico | `docs-planos/74-plano-destravar-executor-agentico-melhorias.md` | Relatório completo de infra do executor de IA: IP+caminho+serviço systemd+uid, caminhos de credencial OAuth (2 serviços), IP+porta do Postgres de produção com lista de 23 bancos de clientes, e um **caminho de escalação de privilégio documentado** (R12) só parcialmente mitigado |
| 🔴 Alto | `tests/test_plano75_quoted_hydration.py:45-46` | Nome completo + contrato + endereço de um **cliente real** ("LUCAS OLIVEIRA SILVA") num fixture de teste |
| 🔴 Alto | `tests/test_plano75_quoted_live.py:31-32` | Idem, segundo cliente real ("MARIA SOUZA") — **achado nesta etapa de planejamento**, não estava no relatório da auditoria original |
| 🟠 Médio | `docs-planos/71-plano-atendente-padrao-por-canal.md` | E-mail pessoal + nome de um funcionário |
| 🟠 Médio | `docs-planos/75-plano-mensagens-em-branco-cloud-e-falha-de-envio.md:122,493` | Nome completo de cliente real (mesmo "LUCAS OLIVEIRA SILVA" acima, citado 2×) |
| 🟠 Médio | `assets/plugin_examples/melhorias/static/ai_section.js:98,107` | IPs internos reais como placeholder de UI — **vai dentro do plugin distribuído** |
| 🟠 Médio | `tests/test_gowa_plugin.py:562,574` + `tests/endpoints/test_p27_gowa_status_reconnect.py:43,73` | Número de telefone real (não segue o padrão sintético do resto da suíte) |
| 🟡 Baixo (opcional) | `docs-planos/65-plano-envio-video-whatsapp-cloud.md:4`, `docs-planos/65-plano-video-correcao-whatsapp-cloud.md:4` | IP do Postgres de produção (faixa privada, não roteável) |
| 🟡 Baixo (opcional) | `docs-planos/70-plano-atribuir-atendente-duvida-curseduca-windmill.md:5,13,17` | Primeiro nome do mesmo funcionário, sem e-mail |
| 🟡 Baixo (opcional) | `docs/analises/05-arquitetura-plugin-analises.md:109` | Mesmo IP do executor |
| 🟡 Baixo (opcional) | `tests/test_endpoints.py:286,291,294,300` | IP de LAN + hostname com nome de um dev (`whatsbot-leandro.teste.techify.run`) |
| ⚪ Decisão de produto | `config/settings.py:31` | Número de WhatsApp da própria Techify (linha de suporte oficial do wizard) — provavelmente OK manter, mas é uma decisão consciente, não um vazamento acidental |

### 6.2 `whatsbot-pro-plugins` — ver sub-plano 02

| Severidade | Onde | O quê |
|---|---|---|
| 🔴 Alto (decisão de escopo) | `plugins/vendas_ia/` (zip + `.json` + entrada em `catalog.json`) | Plugin inteiro construído sob medida para um cliente (Redes Brasil): nome real do banco (`RBNexusDB`), catálogo/funil de vendas reais, código promocional real (`COMBO26RB`) |
| 🔴 Alto (decisão de escopo, mesmo padrão) | `plugins/guarda_ia/` (zip + `.json` + entrada em `catalog.json`) | Plugin irmão, mesma natureza client-specific |
| 🟡 Baixo (só se `vendas_ia` for mantido) | `README.md:28`, `plugins/vendas_ia/vendas_ia.json` | Nome do banco `RBNexusDB` repetido na descrição pública do catálogo |

---

## 7. Perguntas em aberto

| # | Pergunta | Decisão do dono (2026-07-23) |
|---|---|---|
| **P1** | `whatsbot-pro-plugins` também vai ficar público, ou só `whatsbot-pro`? | ✅ **Só o `whatsbot-pro` core.** Sub-plano 02 **não executado** — `whatsbot-pro-plugins` (com `vendas_ia`/`guarda_ia`) segue privado e não-limpo |
| **P2** | `vendas_ia`/`guarda_ia` — remover ou redigir? | ⏸️ **N/A** — consequência de P1 (não se mexeu no repo de plugins) |
| **P3** | `docs-planos/74` — remoção total ou redação? | ✅ **Remoção total** — feita via `--path ... --invert-paths` (sumiu dos 4 branches em todo o histórico) |
| **P4** | Commitar as 6 deleções staged? | ✅ **Sim** — doc 74 saiu por path-removal; as outras 5 (64/65-correcao/70/71/73) viraram 1 commit em `developer` no clone reescrito |
| **P5** | Reescrita cirúrgica vs. squash total? | ✅ **Cirúrgica** — `docs-planos/` preservados, só os itens específicos removidos/genericizados |
| **P6** (novo) | Nome do cliente "Redes Brasil" espalhado + identificadores internos (`RedesBrasilDB_owner`, `RedesBrasil_bot`, `redes.cc`) | ✅ **Genericizar tudo** — "Redes Brasil"→Empresa Exemplo, `redesbrasil`/`redes.cc`→exemplo, etc. |
| **P7** (novo) | E-mails pessoais dos 5 colaboradores na autoria dos commits | ✅ **Anonimizar via `--mailmap`** — `<nome>@users.noreply.github.com` (preserva o 1º nome, remove o e-mail) |
| **P8** (novo) | `tests/test_utm_atendente.py` (client-specific, órfão — carrega plugin ausente) | ✅ **Genericizar e manter** (domínios redes.cc/redesbrasil.net, oferta MTCSE, atendente ia-anna → genéricos) |

---

## Checklist mestre

- [ ] P1–P5 respondidas (ou aceitas as recomendações) antes de iniciar qualquer Fase de reescrita de histórico
- [ ] Sub-plano 01 executado e verificado (§5.3) em `whatsbot-pro`
- [ ] Sub-plano 02 executado e verificado (§5.3) em `whatsbot-pro-plugins` (se P1 = sim)
- [ ] Confirmado com o time que não há clone/fork/CI apontando para o histórico antigo
- [ ] Só então: alternar visibilidade dos repositórios para Public no GitHub
