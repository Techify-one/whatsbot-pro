# Plano 78 — Limpar vazamentos: `whatsbot-pro-plugins` (02 · repositório de plugins)

> **Status:** ⏸️ NÃO EXECUTADO (arquivado) · **Data:** 2026-07-23 · **Escopo:** pequeno/médio (2 plugins + metadados, reescrita de histórico em 1 branch)
>
> **⏸️ DECISÃO (2026-07-23):** o dono escolheu abrir **só o `whatsbot-pro` core** (P1 do mestre). O `whatsbot-pro-plugins` **segue privado e não foi tocado** — `vendas_ia`/`guarda_ia` continuam lá. Este sub-plano fica arquivado; reative-o se um dia decidir abrir o repo de plugins.
> **Origem:** ver mestre [78-plano-limpeza-vazamentos-pre-opensource-00-mestre.md](78-plano-limpeza-vazamentos-pre-opensource-00-mestre.md). Este sub-plano cobre só `Techify-one/whatsbot-pro-plugins` (repo separado, clonado nesta auditoria em `/tmp/.../repo-audit/whatsbot-pro-plugins`, branch única `master`).
> **Método:** conteúdo dos plugins verificado extraindo cada `.zip` versionado no repo (54 blobs únicos ao longo do histórico, cobrindo todas as versões de todos os 13 plugins) e lendo o conteúdo descompactado; metadados (`catalog.json`, `README.md`, `*.json` por plugin) lidos diretamente do checkout.
> **Depende de:** resposta à **Pergunta P1** do mestre (este repo também vai ficar público?). Se a resposta for "não, só o `whatsbot-pro` core", este sub-plano fica adiado indefinidamente — nada aqui precisa ser feito.
>
> **Como usar este plano**: confirme P1 (e a recomendação de P2, abaixo) antes da Fase A. Preencha o "Status de execução" de cada fase antes de seguir para a próxima.

---

## 1. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| **D1** (herdada do mestre, P2) — **a confirmar** | `plugins/vendas_ia/` e `plugins/guarda_ia/` são **removidos inteiramente** do catálogo público (zip + `.json` + entrada em `catalog.json`), não redigidos | Fase A remove os diretórios/entradas em vez de editar o conteúdo dos zips. Se a decisão for "manter", ver §4 (plano B) |

---

## 2. Resumo executivo

O repositório `whatsbot-pro-plugins` guarda cada plugin não-bundled como um `.zip` versionado + um `.json` de metadados, indexados em `catalog.json`. Dois desses plugins (`vendas_ia`, `guarda_ia`) são builds feitas sob medida para um cliente específico (Redes Brasil/"BIA") — não são plugins de exemplo genéricos como os demais 11. `vendas_ia` em particular expõe o nome real de um banco interno (`RBNexusDB`), um código promocional real (`COMBO26RB`) e a lógica de negócio de um funil de vendas real. A auditoria confirmou (via `gitleaks` + leitura completa dos zips extraídos) que **nenhum outro plugin do catálogo** tem conteúdo sensível equivalente.

A recomendação (D1) é remover os dois plugins do catálogo público em vez de tentar redigir — eles são inteiramente client-specific, então "redigir" significaria reescrever a lógica de negócio inteira, não só trocar strings. Isso também resolve de graça os 3 itens de baixo-risco (`RBNexusDB` mencionado em `README.md`/`vendas_ia.json`).

---

## 3. Inventário detalhado (plano A — remoção, D1 confirmado)

| Item a remover | Onde |
|---|---|
| `plugins/vendas_ia/vendas_ia.zip` | arquivo binário |
| `plugins/vendas_ia/vendas_ia.json` | metadados |
| Linha da tabela + entrada em `catalog.json` referente a `vendas_ia` | `README.md` (tabela "Plugins disponíveis"), `catalog.json` |
| `plugins/guarda_ia/guarda_ia.zip` | arquivo binário |
| `plugins/guarda_ia/guarda_ia.json` | metadados |
| Linha da tabela + entrada em `catalog.json` referente a `guarda_ia` | `README.md`, `catalog.json` |

Removendo os dois, os seguintes achados de baixo-risco da auditoria ficam **automaticamente resolvidos** (não precisam de ação própria): `README.md:28` (menção a `RBNexusDB`), `plugins/vendas_ia/vendas_ia.json` (mesma menção), o exemplo de preço em `seed_prompts/comercial.md`, o exemplo de código de oferta em `tool_code/pesquisar_informacoes_cursos.py`, e o código promocional real em `seed_prompts/roteador.md`.

---

## 4. Plano B (só se D1 for revertida para "manter, redigir")

Se a decisão final for **manter** `vendas_ia`/`guarda_ia` no catálogo público, a lista de substituições de texto seria (uma nova versão do zip precisaria ser gerada — `git-filter-repo --replace-text` só reescreve o *histórico de blobs já commitados*, não abre e edita o conteúdo de um `.zip` corrente automaticamente; cada versão do zip é um blob binário independente, então a correção teria que ser feita **na fonte** — recompactar um novo zip corrigido, versionar como nova versão do plugin — e só então, se ainda se quiser limpar as versões **antigas** do zip do histórico, usar `--replace-text` sobre os blobs binários, o que o `git-filter-repo` suporta mas com uma ressalva: ele precisa reconhecer o zip como texto para aplicar `--replace-text`, o que **não funciona em arquivos binários comprimidos** — nesse caso a única forma de limpar o histórico seria remover as versões antigas do zip com `--path ... --invert-paths` e manter só a versão corrigida mais recente):

| String antiga | String nova sugerida |
|---|---|
| `RBNexusDB` | `NexusDB` (genérico) |
| `COMBO26RB` | `PROMO_EXEMPLO` |

⚠️ Este caminho é significativamente mais trabalhoso e ainda deixa uma pergunta em aberto (zips antigos no histórico continuam com o conteúdo original a menos que sejam removidos por path) — por isso D1 recomenda a remoção total em vez deste plano B.

---

## 5. Fases (assumindo D1 = remoção)

```
FASE A → FASE B → FASE C → FASE D → FASE E      ← sequencial
```

### Fase A — Remover os plugins do catálogo (working tree + commit)

**Objetivo:** `vendas_ia`/`guarda_ia` fora do catálogo público, no checkout atual.

**Itens:**
1. `[sequencial]` `git rm -r plugins/vendas_ia plugins/guarda_ia` no checkout local do `whatsbot-pro-plugins`.
2. `[sequencial]` Editar `catalog.json` removendo as duas entradas correspondentes.
3. `[sequencial]` Editar `README.md`, removendo as duas linhas da tabela "Plugins disponíveis" (`vendas_ia`, `guarda_ia`).
4. `[sequencial]` Commitar (ex.: `chore: remover plugins client-specific (vendas_ia, guarda_ia) antes de abrir o repo (plano 78)`).

**Pronto quando:** `ls plugins/` não lista mais `vendas_ia`/`guarda_ia`; `catalog.json` e `README.md` não citam mais os dois; commit feito.

#### Status de execução — Fase A
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(preencher ao executar)_
- **Problemas / pendências:** _(preencher ao executar)_
- **Verificação:** _(preencher ao executar)_

---

### Fase B — Clone dedicado para reescrita de histórico

**Objetivo:** ambiente isolado com o branch `master` do `origin` trazido como local.

**Itens:**
1. `[sequencial]` `git clone --no-local https://github.com/Techify-one/whatsbot-pro-plugins.git /tmp/whatsbot-pro-plugins-rewrite`.
2. `[sequencial]` Sanidade pré-rewrite: `git -C /tmp/whatsbot-pro-plugins-rewrite log --all --oneline -- plugins/vendas_ia plugins/guarda_ia | wc -l` — anotar o número de commits que tocam esses caminhos (baseline).

**Pronto quando:** clone existe e o baseline foi anotado.

#### Status de execução — Fase B
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(preencher ao executar)_
- **Problemas / pendências:** _(preencher ao executar)_
- **Verificação:** _(preencher ao executar)_

---

### Fase C — Rodar `git-filter-repo`

**Objetivo:** remover `plugins/vendas_ia/` e `plugins/guarda_ia/` de **todo** o histórico do branch `master` (todas as versões antigas dos zips, não só a atual).

**Itens:**
1. `[sequencial]` Dentro de `/tmp/whatsbot-pro-plugins-rewrite`:
   ```
   git-filter-repo --path plugins/vendas_ia --path plugins/guarda_ia --invert-paths
   ```
2. `[sequencial]` Conferir: `git log --all --oneline -- plugins/vendas_ia plugins/guarda_ia` deve retornar vazio.

**Pronto quando:** o comando termina sem erro e a busca pelos dois diretórios não retorna nada em nenhum commit.

#### Status de execução — Fase C
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(preencher ao executar)_
- **Problemas / pendências:** _(preencher ao executar)_
- **Verificação:** _(preencher ao executar)_

---

### Fase D — Verificação

**Objetivo:** confirmar que nada de `vendas_ia`/`guarda_ia` sobrevive, e que o catálogo continua consistente para os 11 plugins restantes.

**Itens:**
1. `[sequencial]` `gitleaks detect --source /tmp/whatsbot-pro-plugins-rewrite --log-opts="--all"` — esperado limpo (já estava limpo antes).
2. `[sequencial]` `git -C /tmp/whatsbot-pro-plugins-rewrite log --all -p | grep -ci "RBNexusDB\|COMBO26RB"` — esperado **0**.
3. `[sequencial]` Conferir que `catalog.json` ainda é um JSON válido e lista exatamente os 11 plugins restantes (`python3 -c "import json; d=json.load(open('catalog.json')); print(len(d.get('plugins', d)))"` ou equivalente, ajustando à chave real do arquivo).

**Pronto quando:** os 3 itens confirmados.

#### Status de execução — Fase D
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(preencher ao executar)_
- **Problemas / pendências:** _(preencher ao executar)_
- **Verificação:** _(preencher ao executar)_

---

### Fase E — Force-push

**Objetivo:** enviar o histórico reescrito para `origin/master`.

**Itens:**
1. `[sequencial]` `git remote add origin https://github.com/Techify-one/whatsbot-pro-plugins.git` (removido automaticamente pelo filter-repo).
2. `[sequencial]` `git push origin --force --all`.
3. `[sequencial]` `git push origin --force --tags` (só se houver tags).

**Pronto quando:** `master` no GitHub reflete o catálogo sem `vendas_ia`/`guarda_ia` e sem histórico antigo desses plugins.

#### Status de execução — Fase E
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(preencher ao executar)_
- **Problemas / pendências:** _(preencher ao executar)_
- **Verificação:** _(preencher ao executar)_

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Clientes que já instalaram `vendas_ia`/`guarda_ia` via `Importar (.zip)` | Remover do catálogo público não afeta instalações existentes (o plugin já importado continua rodando normalmente) — mas ninguém mais vai conseguir baixar/reimportar pela tela Gerenciar Plugins depois disso | Confirmar que isso é aceitável (provavelmente sim, já que são plugins de 1 cliente só); se precisar continuar distribuindo para esse cliente especificamente, manter uma cópia fora deste repo público (ex.: enviar o `.zip` diretamente) |
| Referência cruzada no `whatsbot-pro` core | O `CLAUDE.md`/memória do projeto principal cita `vendas_ia`/`guarda_ia` como exemplos do "Repositório de plugins do Pro" | Não é um vazamento de dado, mas vale atualizar a documentação do core depois, fora do escopo deste plano de limpeza |
| Convenção "Canal · " e nomes em 3 lugares | Não se aplica a `vendas_ia`/`guarda_ia` (nenhum dos dois é plugin de canal) — sem risco de quebrar a convenção dos outros 11 | — |

---

## Checklist de verificação

- [ ] P1 confirmada (este repo vai ficar público) e D1 confirmada (remoção, não redação)
- [ ] Fase A: `vendas_ia`/`guarda_ia` removidos do working tree/catálogo, commit feito
- [ ] Fase B: clone de rewrite criado, baseline anotado
- [ ] Fase C: `git-filter-repo` rodado, os 2 diretórios sumiram de todo o histórico
- [ ] Fase D: `gitleaks` limpo, grep de sanidade = 0, `catalog.json` válido com 11 plugins
- [ ] Fase E: force-push feito em `master` (+ tags se houver)
- [ ] Confirmado se algum cliente precisa continuar recebendo `vendas_ia`/`guarda_ia` por fora deste repo
