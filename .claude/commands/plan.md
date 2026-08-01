Você vai gerar um **plano de implementação** para o WhatsBot, num arquivo `.md` em `docs-planos/`, seguindo o padrão dos planos 23–24 deste repositório. O plano é escrito para ser executado por **outra IA** (ou por você numa sessão futura): auto-contido, verificado contra o código real (`arquivo:linha`), tabular, com paralelização explícita e rastreamento de progresso embutido.

O que o usuário quer resolver/implementar: **$ARGUMENTS**

Se `$ARGUMENTS` estiver vazio ou vago demais para planejar, faça 2–4 perguntas curtas de escopo ANTES de investigar (qual tela/módulo, comportamento esperado, restrição de backend/DB, há decisões já tomadas). Não invente escopo.

## Passo 0 — Estude o padrão antes de escrever

1. Leia o `CLAUDE.md` da raiz (arquitetura, convenções, gotchas). O plano DEVE respeitar essas regras.
2. Liste `docs-planos/` e leia 1–2 planos recentes (maior número, ex: `24-plano-*`) como **referência de formato e profundidade** — imite a estrutura deles. Para um esforço grande, veja `23-plano-refatoracao-00-mestre.md` (padrão mestre + sub-planos + waves).
3. Descubra o **próximo número**: maior prefixo `NN-` em `docs-planos/` → use `NN+1`. Nome do arquivo: `docs-planos/<NN>-plano-<slug-kebab-curto>.md`. Para esforço grande, gere um mestre `<NN>-plano-<slug>-00-mestre.md` + sub-planos `<NN>-plano-<slug>-0X-<area>.md` com um **Índice dos sub-planos** no mestre.

## Passo 1 — Investigue o código (NÃO pule, é o que separa um bom plano de um chute)

Antes de propor qualquer coisa, **leia o código real**. Toda afirmação (gap, causa, ponto de mudança, diagnóstico) vem com `arquivo:linha` **verificado** — nunca de memória nem estimado. Diagnóstico de tamanho/escala é **medido** (`wc -l`, `grep -c`), não chutado.

Para tarefas amplas, lance sub-agentes `Explore`/`general-purpose` **em paralelo** (num único bloco de tool calls) para varrer áreas independentes — backend, frontend, plugins, DB/migrations, testes — e voltar com os `arquivo:linha` relevantes.

Identifique: estado atual, causa-raiz, pontos exatos de mudança, dependências entre partes, e o que é **falso-positivo** (algo que parece problema mas não é — descarte explicitamente, com a razão).

## Passo 2 — Escreva o plano (.md)

Adapte a profundidade ao tamanho da tarefa (uma correção pequena pode fundir seções; um refactor grande usa todas + mestre/sub-planos). **Nunca omita:** Fases, paralelização explícita, e o rastreamento de execução (Passo 3). Use **tabelas em vez de prosa** sempre que listar itens.

Seções na ordem (numeradas `## 0`, `## 1`, …, estilo 23–24):

1. **Título** — `# Plano <NN> — <objetivo em uma linha>`.
2. **Bloco de status no topo** (logo após o título), em blockquote:
   ```
   > **Status:** PLANEJAMENTO · **Data:** <YYYY-MM-DD> · **Escopo:** <pequeno/médio/grande>
   > **Origem:** <pedido do usuário / plano relacionado>. **Método:** <como foi verificado, ex: leitura + grep>.
   > <2–3 linhas sobre o que está sendo feito e por quê>
   ```
3. **Decisões do usuário / travadas (não reabrir)** — se houver decisões já tomadas, tabela `| # | Decisão | Consequência no plano |` com `D1/D2…` e ✅, datadas. Inclui princípios fixos (ex: "nada em produção ⇒ refactor agressivo, sem stopgap").
4. **Resumo executivo** — 3–6 linhas: o problema e a forma da solução.
5. **Como funciona hoje (mapa)** — estado atual com `arquivo:linha`; ⚠️ destaque gotchas que tornam algo obrigatório.
6. **Inventário / análise** — tabela(s) dos itens a fazer, cada linha com `arquivo:linha`, o que falta, abordagem, **Risco** (baixo/médio/alto) e **Esforço** (S/M/L). Seção **"Falsos positivos descartados"** com a razão de cada.
7. **Mudanças de infraestrutura** (se houver) — refactors habilitadores, separados por camada (backend / frontend / DB / plugins).
8. **Fases / Roadmap** — ver Passo 2b abaixo (o coração do plano).
9. **Riscos e cuidados** — tabela `| Ponto | Risco | Mitigação |`. Cubra o que se aplicar: colisões, loops, ordem de migration, comportamento no Postgres (único backend), modo escuro, restart de plugin, segredos na URL, regressão de evento/filtro.
10. **Perguntas em aberto** — numeradas `P1, P2, …`, cada uma com `✅ DECIDIDO (data): …` ou `⏸️ ADIADO`, contexto, opções (a)(b) e recomendação.
11. **Apêndice — arquivos-chave** — lista dos arquivos que o executor vai tocar, agrupados por camada.

### Passo 2b — Fases, waves e PARALELIZAÇÃO (regra obrigatória)

Quebre em **fases executáveis e ordenadas**. Para esforços com várias frentes, agrupe as fases em **Waves/Ondas** e mostre as dependências num **diagrama ASCII** (estilo §6 do plano mestre 23) + uma **tabela de fases**:

```
WAVE 0  A0 ─ A1 · B0(habilitador)          ← tudo nesta linha roda em paralelo
           │  (barreira: A1 bloqueia C1)
WAVE 1  C1 → C2 · D0                         ← C2 depende de C1; D0 é independente
```

Tabela de fases (colunas: Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando / Sub-plano):

- **🟢 = PODE AGRUPAR** (sem dependência — despache junto com as outras 🟢 da mesma wave).
- **🔴 = FAÇA SOZINHA** (sequencial / bloqueante — não paralelize).
- Marque dependências explícitas: `[depende de: X]`, `[bloqueia: Y]`.

Para cada fase, um bloco:
- **Objetivo** (uma linha).
- **Itens** (passos concretos com `arquivo:linha`). Dentro da fase, marque o que é `[paralelo]` vs `[sequencial]`.
- **Pronto quando** — critério de aceitação **observável e testável** (não especulativo): o que recarregar/clicar/rodar e o que deve acontecer; quais testes ficam verdes.

Regras de disciplina a citar quando couber (são as do repo): **verde a cada fase**; **caracterização ANTES** de mexer em fluxo crítico; **um refactor por commit**; nunca avançar com teste vermelho não-explicado.

## Passo 3 — Rastreamento de execução embutido (REGRA OBRIGATÓRIA — exigência deste comando)

Os planos atuais do repo **não** têm rastreamento de "o que foi feito por etapa" — este comando **padroniza isso**. No bloco de status do topo, inclua a instrução `> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.`

Ao final de **cada fase**, inclua este bloco para o executor preencher:

```
#### Status de execução — Fase <N>
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_
```

Legenda de estado de execução (distinta dos 🟢/🔴 de paralelização e dos ✅ de decisão): `⬜ Não iniciada` · `🟡 Em andamento` · `✅ Concluída` · `⛔ Bloqueada`.

## Passo 4 — Checklist de verificação

Termine o plano com um **Checklist de verificação** (`- [ ]`) aplicável a cada mudança, conforme a tarefa: reload/back-forward, `python -m pytest` nas camadas afetadas, `node --test` nos módulos puros, **suíte verde no Postgres** (`WHATSBOT_TEST_DB_URL`), runner do plugin quando aplicável, modo escuro legível (telas novas), migration round-trip, restart de plugin, sem segredo na URL.

## Regras finais

- Plano em **português BR**; nomes de arquivos/símbolos/código em inglês como no resto do repo.
- **Não escreva código de implementação** — descreva o quê e o onde (`arquivo:linha`). Trechos curtos ilustrativos (assinatura, schema) são ok; nada de patches grandes.
- Tudo verificado contra o código real. O que não deu pra confirmar vira "a confirmar", não afirmação.
- Salve em `docs-planos/<NN>-plano-<slug>.md` (mestre + sub-planos se grande). Ao final, mostre ao usuário: caminho(s) do arquivo, resumo das waves/fases, e **o que pode ser paralelizado**.
- **Só gere o plano** — não comece a implementar. Se o usuário quiser executar, ele pede depois.
