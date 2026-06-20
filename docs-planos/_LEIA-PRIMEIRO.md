# _LEIA-PRIMEIRO — Como usar esta pasta (e a `docs-pesquisa/`)

> Ponto de entrada único do planejamento do **WhatsBot Pro**. Qualquer pessoa **ou IA** que vá
> reconciliar planos, escrever código ou rodar um workflow começa por aqui. Última atualização:
> **2026-06-19** (HEAD `58586e1`).

---

## 1. Regra de precedência (quando os docs divergirem)

Os documentos foram escritos em momentos diferentes; alguns envelheceram. **Quando dois docs se
contradisserem, a ordem de verdade é:**

```
código real (origin/main)
  > _REAVALIACAO-capability-map.md   (o que existe no código, verificado)
  > _REAVALIACAO-relatorio.md        (análise executiva da realidade)
  > DECISOES.md                      (decisões do Thiago — Lote 1/2/3)
  > planos 01–10                     (fonte de verdade de fases/migrations/endpoints)
  > docs-pesquisa/                   (o "porquê" / DDL / trade-offs — NUNCA sequência ou decisão)
```

Regras práticas:
- **`docs-pesquisa/` nunca dita sequência nem decisão.** Use só para DDL detalhado e racional quando
  um plano apontar para ela (ex.: "ver doc 06 §4"). Seu "faseamento sugerido" e suas "perguntas em
  aberto" estão **superados** pelos planos e pelo `DECISOES.md`.
- **O plano-mestre (`00`) está com a ordem das ondas DESATUALIZADA.** A sequência viva é a do
  `_REAVALIACAO-relatorio.md §4` (re-sequenciada após o motor AGNO ter shipado fora de ordem).
- **Decisão fechada não se re-litiga.** Se uma pergunta `P*` aparece como ✅ no `DECISOES.md`, é
  decisão — não reabrir sem o Thiago.

---

## 2. Estado real do código (para não perder tempo)

- **Working tree = `origin/main` = `58586e1`.** Sem `git pull` pendente. A nota "5 commits atrás" no
  topo do `_REAVALIACAO-relatorio.md` é **histórica/obsoleta** — ignore-a.
- **O motor AGNO + AI engine config-in-DB JÁ ESTÁ NO CÓDIGO** (`agent/agno_engine.py`,
  `agent/agent_factory.py`, `agent/ai_tool_installer.py`, tabelas `ai_*`). Operando **por `phone`**
  (sem inbox/conversa/RBAC). Flag `ai_engine_enabled` (default OFF).
- **O RCE do code-in-DB JÁ ESTÁ MITIGADO POR PADRÃO.** O installer só roda se
  `ai_tools_code_enabled=True` (default **False**, env `WHATSBOT_AI_TOOLS_CODE`) —
  ver `server/app.py:116` e `config/settings.py`. Tools criadas via API nascem `enabled=False`.
  → **O checklist "P0 — gate admin-only" do `_REAVALIACAO-relatorio.md §6` está OBSOLETO**: a
  mitigação (kill-switch) shipou *depois* daquele relatório. Ver `DECISOES.md` Lote 3 (P62), que é
  a verdade atual.

---

## 3. Índice dos documentos (o que é cada um e como tratá-lo)

### `docs-planos/` — o "em que ordem" e "o quê"

| Documento | Papel | Como tratar |
|---|---|---|
| `_LEIA-PRIMEIRO.md` | Este arquivo | Ponto de entrada |
| `_REAVALIACAO-capability-map.md` | O que existe no código, verificado | **Fonte de verdade do código** |
| `_REAVALIACAO-relatorio.md` | Análise executiva da realidade pós-AGNO | Verdade fresca — *exceto* o checklist P0 (ver §2) e a nota "5 commits atrás" |
| `DECISOES.md` | Decisões do Thiago (Lote 1/2/3) | **Fonte de verdade das decisões** |
| `00-plano-mestre.md` | Orquestração das ondas | Manter, mas **ondas desatualizadas** — sequência viva = relatório §4 |
| `01`–`09` (planos por feature) | Fases/migrations/endpoints por feature | Fonte de verdade da feature; serão reconciliados contra o código |
| `10-plano-frontend-ux.md` | Roteiro de frontend consolidado | Ativo |
| `REF-gerenciamento-ia-code-in-db.md` | Referência do padrão code-in-DB (gerenciamento-ia) | Referência (nota: P64/`output_schema` foi rebaixado — ver DECISOES Lote 3) |
| `_archive/` | Docs cuja função terminou | **Não usar como referência de estado** |

### `docs-pesquisa/` — o "porquê" (referência, não decisão)

8 docs (`00`–`08`), o racional + DDL detalhado por feature. **Manter, mas só como consulta** quando um
plano referencia (precedência §1). Não confiar no "faseamento sugerido" nem nas "perguntas em aberto"
de lá — estão superados.

---

## 4. Inconsistências conhecidas a resolver na reconciliação (WF1)

Itens onde os docs divergem entre si ou do código — cada um precisa virar um achado verificado:

1. **Gate do code-in-DB**: relatório diz "fazer gate admin-only (não feito)"; DECISOES Lote 3 +
   código dizem "kill-switch `ai_tools_code_enabled` default OFF — FEITO". → **Código vence**: está
   mitigado por padrão; o que falta é (a) separação de papéis (RBAC, plano 03) e (b) isolamento por
   subprocesso (retrofit P62/P67 sobre o plano 09).
2. **Migrations 0007/0008 consumidas** pelo AGNO/`pkg_deps`. Planos 01/02/03/04/05 que reservavam
   esses slots precisam renumerar para **0009+**, encadeando a partir do head real
   `0008_plugin_installed_deps` (P82 linear).
3. **`executions.agent_key/total_tokens/total_cost_usd`**: colunas criadas, writer não popula.
4. **`server/dev.py`** não passa `ai_engine_enabled` ao handler (config-in-DB nunca liga no dev).
5. **`agno`/`openai` sem pin** no `requirements.txt`.
6. **CLAUDE.md desatualizado** (lista 8 plugins bundled e "11 tabelas"; real: só `lembretes`, 15+
   tabelas com `ai_*`/`tool_overrides`; não menciona o motor AGNO).
7. **Planos 01/03/04/05/08/09** afirmados "intactos" mas **não verificados** contra o código atual —
   é o alvo principal do WF1.

---

## 5. Decisões de sessão (2026-06-19, trilha escolhida: **Pro completo**)

- **Trilha**: Pro completo (plano-mestre inteiro), com as ondas **re-sequenciadas** pelo relatório §4.
- **Auditoria (plano 07)**: **mantida adiada** (P68–P75) — fora do WF1 e do MVP.
- **P62 (code-in-DB)**: postura em camadas — kill-switch (já feito) → isolamento por subprocesso na
  onda do retrofit (sobre o `SubprocessService`, plano 09) → separação de papéis quando o RBAC
  (plano 03) chegar. Mantém a feature "tools no banco", mas só admin edita e o runner roda isolado.
