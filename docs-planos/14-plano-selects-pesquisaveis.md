# Plano de Implementação — Selects pesquisáveis e consistência de dropdowns — WhatsBot Pro

> **Status:** PLANO acionável — **melhoria de UX no frontend** (não greenfield, não toca backend).
> Deriva da investigação de 2026-06-20 sobre campos `<select>` nativos com listas longas/dinâmicas.
> **Tenancy:** uma empresa, servidor único, multi-usuário (irrelevante aqui — é só frontend).
>
> **Problema (origem):** o usuário reportou dois `<select>` nativos difíceis de usar:
> 1. **"Modelo" no editor de agentes** (`/ai`) — lista **centenas** de modelos LLM num `<select>` nativo, sem busca.
> 2. **"Prioridade"** (atributo customizado tipo `list`) — `<select>` nativo cujo popup é renderizado pelo SO
>    (fundo branco mesmo no dark mode).
>
> **Objetivo:** dar **barra de busca** aos selects com muitas opções e padronizar os dropdowns custom já
> existentes, sem reescrever os selects de enum curto (que estão OK como nativos).

---

## Estado atual (2026-06-20)

> Verificado por investigação read-only (grep `<select` em `web/static/js` + leitura dos componentes).

O frontend **já possui 3 dropdowns custom com busca** que servem de molde — **não precisamos inventar nada**:

| Componente | Padrão | Reusabilidade |
|---|---|---|
| [ModelSelect.js](../web/static/js/components/ModelSelect.js) | input pesquisável + lista flutuante + navegação por teclado + cache global de `/api/models` | **Específico de modelos** (acopla `getModels`, `input_modalities`). Já usado em [ConfigPanel.js:289](../web/static/js/components/ConfigPanel.js#L289). |
| [AssigneePicker.js](../web/static/js/components/contacts/AssigneePicker.js) | botão + dropdown + caixa de busca + seções (humanos / IA) | Específico de atribuição, mas é o melhor molde de **dropdown-com-busca genérico**. |
| [TagPicker.js](../web/static/js/components/contacts/TagPicker.js) / [ConversationLabelEditor.js](../web/static/js/components/contacts/ConversationLabelEditor.js) | busca + multi-seleção (chips) + criar inline | Para tags/etiquetas; multi-seleção. |

**Não existe** um `<SearchableSelect>` genérico (single-select, options arbitrárias) — é a peça que falta para reuso.

### Inventário completo dos `<select>` nativos

| # | Local | Campo | Veredito |
|---|-------|-------|----------|
| 1 | [AgentsManager.js:200-206](../web/static/js/components/ai/AgentsManager.js#L200) | **Modelo** | 🔴 **Precisa busca** — centenas de opções. `ModelSelect` já existe; só não está fiado aqui. |
| 2 | [CustomAttributeField.js:37](../web/static/js/components/contacts/CustomAttributeField.js#L37) | atributo tipo `list` (ex.: Prioridade) | 🟡 Busca **quando `options` é longo** (lista definida pelo usuário pode ter dezenas de itens). |
| 3 | [FilterBar.js:277-289](../web/static/js/components/FilterBar.js#L277) | filtro **Responsável** (assignee) | 🟡 Lista **todos os usuários**; cresce com a equipe. |
| 4 | [AuditLog.js:238](../web/static/js/components/AuditLog.js#L238) / [246](../web/static/js/components/AuditLog.js#L246) | filtros **Recurso** / **Ação** | 🟡 Listas dinâmicas que crescem; tela admin (prioridade menor). |
| 5 | [AgentsManager.js:188](../web/static/js/components/ai/AgentsManager.js#L188) | **Prompt** | 🟢 OK nativo (poucos; cresce devagar). Reavaliar se virar lista grande. |
| 6 | [ConfigPanel.js:243](../web/static/js/components/ConfigPanel.js#L243) / 356 / 369 | grupos, modo/destino transcrição | 🟢 OK nativo (enum fixo curto). |
| 7 | [ConversationFilterBar.js:132](../web/static/js/components/contacts/ConversationFilterBar.js#L132) / 176 / 184 | status, ordenação | 🟢 OK nativo. |
| 8 | [CustomAttributesManager.js:133](../web/static/js/components/CustomAttributesManager.js#L133) / 141 | tipo, escopo | 🟢 OK nativo. |
| 9 | [ChannelsManager.js:107](../web/static/js/components/ChannelsManager.js#L107) | provider | 🟢 OK nativo. |
| 10 | [Executions.js:263](../web/static/js/components/Executions.js#L263) | status | 🟢 OK nativo. |
| 11 | [TemplatePicker.js:483](../web/static/js/components/contacts/TemplatePicker.js#L483) | categoria | 🟢 OK nativo (2 opções). |
| 12 | [NewConversationModal.js:187](../web/static/js/components/contacts/NewConversationModal.js#L187) | caixa de entrada | 🟢 OK nativo (poucos canais). |
| 13 | [PluginSettingsForm.js:33](../web/static/js/components/PluginSettingsForm.js#L33) | enum de plugin | 🟢 OK nativo (definido pelo plugin). |
| 14 | [AuditLog.js:254](../web/static/js/components/AuditLog.js#L254) | tipo de ator | 🟢 OK nativo (3 opções). |

### Nota sobre dark mode (importante)

Todos os selects usam `.wa-field`/`bg-wa-*` → o **controle fechado** já segue o tema. **Mas** o *popup de opções*
de um `<select>` nativo é desenhado pelo SO e **não pode ser estilizado por CSS** (por isso o fundo branco/azul
do sistema nas screenshots). Converter para dropdown custom resolve **busca + tema** de uma vez. Os itens 🟢 não
justificam a conversão só por isso (são curtos e o popup nativo aceitável); os 🟡/🔴 sim.

---

## Legenda de fases

| Fase | Estado | Observação |
|------|--------|------------|
| **Fase 1** — fiar `ModelSelect` no editor de agentes (item 1) | **nao_feito** | Win imediato, ~10 linhas, sem componente novo. |
| **Fase 2** — extrair `<SearchableSelect>` genérico | **nao_feito** | Generaliza o `ModelSelect`. Base das fases 3+. |
| **Fase 3** — aplicar busca aos candidatos 🟡 (itens 2, 3, 4) | **nao_feito** | Depende da Fase 2. |
| **Fase 4** (opcional) — refatorar `ModelSelect` sobre o genérico | **nao_feito** | Higiene de código; sem mudança visível. |

> **Drift de linhas:** todas as âncoras de linha foram conferidas em 2026-06-20, mas **use `grep` por nome de
> função/símbolo na implementação**, nunca a linha hardcoded.

---

## Fase 1 — Win imediato: `ModelSelect` no editor de agentes

**Arquivo:** [web/static/js/components/ai/AgentsManager.js](../web/static/js/components/ai/AgentsManager.js)

O componente `AgentForm` já recebe a prop `models` (carregada via `getModels()` em `listAgents()…getModels()`,
~linha 321) e tem o estado `model`/`setModel`. Hoje renderiza um `<select>` nativo (~linhas 200-206).

**Passos:**
1. Importar o componente:
   ```js
   import { ModelSelect } from '../ModelSelect.js';
   ```
   (ajustar o path relativo — `AgentsManager.js` está em `components/ai/`, então `../ModelSelect.js`).
2. Substituir o bloco `<select … value=${model} …>…</select>` (campo **Modelo**) por:
   ```js
   <${ModelSelect}
     value=${model}
     onChange=${setModel}
     placeholder="— padrão do app —"
   />
   ```
3. **Detalhe a preservar:** o select atual tem a opção `— padrão do app —` (`value=""`) para herdar o modelo
   global, e uma opção sintética `${model} (atual)` quando o modelo salvo não está na lista. O `ModelSelect`
   atual **não** representa o estado "vazio = padrão" nem mostra um valor fora da lista de forma idêntica.
   - Mínimo: o `placeholder` cobre o estado vazio visualmente; `onChange('')` precisa continuar possível.
   - **Decisão necessária:** ou (a) aceitar que limpar o campo = usar padrão (input vazio → `onChange('')`),
     ou (b) estender `ModelSelect` com uma prop `allowEmpty`/`emptyLabel`. Recomendo (b) pequeno, para não
     perder a semântica "padrão do app" — ver Fase 2/4.
4. A prop `models` deixa de ser usada pelo `<select>`, mas `ModelSelect` busca via cache próprio
   (`fetchModelsOnce`) — **não remover** o `getModels()` do carregamento se outras partes usam; só o `<select>`
   deixa de depender de `models`. (Conferir: hoje `models` só alimenta esse select — pode virar dead prop.)

**Validação:** abrir `/ai` → Editar agente → campo Modelo deve ser um input pesquisável; digitar "deepseek"
filtra; selecionar salva o `id`; reabrir mostra o nome do modelo; dark mode com popup temizado.

---

## Fase 2 — Componente genérico `<SearchableSelect>`

**Novo arquivo:** `web/static/js/components/SearchableSelect.js`

Generaliza o `ModelSelect` para qualquer lista single-select. **Não** acopla `getModels`.

**API proposta:**
```js
SearchableSelect({
  value,            // valor atual (string)
  onChange,         // (newValue) => void
  options,          // [{ value, label, sublabel? }]  — já materializadas pelo chamador
  placeholder,      // texto quando vazio
  allowEmpty,       // bool — mostra a opção "limpar/—" no topo
  emptyLabel,       // rótulo da opção vazia (ex.: "— padrão do app —")
  searchPlaceholder,// placeholder da caixa de busca
  disabled,         // bool
})
```

**Comportamento (copiar do `ModelSelect`):**
- input que vira caixa de busca ao focar; lista flutuante `absolute z-50` com `max-h` + `wa-scrollbar`.
- filtro case-insensitive sobre `label` (e `sublabel`/`value` se presente).
- navegação por teclado: ↑/↓ move highlight, Enter seleciona, Esc fecha; `scrollIntoView` no highlight.
- fechar em clique externo (`mousedown` no `document`).
- exibe `label` do valor atual quando fechado; `value` cru se não houver match (com `title` no hover).
- **dark-mode-safe**: classes `bg-wa-panel`/`bg-wa-bg`/`text-wa-text`/`border-wa-border`/`hover:bg-wa-hover`
  (idênticas ao `ModelSelect`).
- `allowEmpty` → primeira linha da lista limpa a seleção (`onChange('')`) e mostra `emptyLabel`.

**Reuso de molde:** estruturalmente é o `ModelSelect` sem o `fetchModelsOnce`/`filterModality` e recebendo
`options` por prop. Manter o componente **burro** (sem fetch) — quem busca os dados é o chamador.

---

## Fase 3 — Aplicar busca aos candidatos 🟡

> Todos dependem da Fase 2. Cada item é independente; pode ir em PRs separados.

### 3.1 — Atributo customizado tipo `list` (item 2)
**Arquivo:** [web/static/js/components/contacts/CustomAttributeField.js](../web/static/js/components/contacts/CustomAttributeField.js)
- No ramo `type === 'list'` (~linha 35), trocar o `<select>` por `<SearchableSelect>` **somente quando a lista
  for longa** — ex.: `def.options.length > 8`. Para listas curtas (Prioridade = 3), manter o `<select>` nativo
  (mais leve, sem overhead de dropdown flutuante).
- `options = def.options.map(o => ({ value: o, label: o }))`, `allowEmpty=true`, `emptyLabel="— selecione —"`.
- **Atenção a layout**: este campo aparece no painel lateral da conversa/contato (espaço estreito) — conferir
  que o dropdown flutuante não estoura o container (`z-index` e `overflow` do painel).

### 3.2 — Filtro "Responsável" (item 3)
**Arquivo:** [web/static/js/components/FilterBar.js](../web/static/js/components/FilterBar.js) (ramo `dim.kind === 'assignee'`, ~linha 273)
- `options` = opções fixas (`Todas`, `Minhas`, `Atribuídas`, `Não atribuídas`) **+** `users.map(...)`.
- Mais simples: manter os 4 fixos como cabeçalho e dar busca só sobre usuários — ou materializar tudo em
  `options` e deixar o `SearchableSelect` filtrar. Recomendo materializar tudo (simples).
- Conferir o valor: hoje usa `value=${value}` com `""`/`me`/`present`/`none`/`String(u.id)`.

### 3.3 — Filtros de auditoria (item 4)
**Arquivo:** [web/static/js/components/AuditLog.js](../web/static/js/components/AuditLog.js) (selects **Recurso** ~238 e **Ação** ~246)
- `options` de `resourceTypes`/`actions` (já carregados), `allowEmpty=true` com `"Todos os recursos"` /
  `"Todas as ações"`. Prioridade menor (tela admin) — fazer por último.

---

## Fase 4 (opcional) — Refatorar `ModelSelect` sobre o genérico

Higiene: reescrever `ModelSelect` como um wrapper fino que (a) busca via `fetchModelsOnce`/`filterModality`,
(b) materializa `options=[{value:id, label:name, sublabel:id}]`, (c) delega a renderização ao `SearchableSelect`.
Mantém a API pública (`value/onChange/filterModality/placeholder`) → **zero mudança** nos call sites
(ConfigPanel, AgentsManager). Sem mudança visível; só remove duplicação. Pode ser adiado indefinidamente.

---

## Fora de escopo

- **Não** converter os selects 🟢 (enums curtos) — o nativo é adequado e mais leve.
- **Não** mexer no backend nem em `/api/models`/`/api/users` (dados já existem).
- **Não** tornar multi-select (isso é o domínio do `TagPicker`/`ConversationLabelEditor`).
- **Não** alterar selects de plugins de exemplo (`assets/plugin_examples/*`) — fora do core; cada plugin segue
  as mesmas regras de dark mode por conta própria.

---

## Checklist de validação (todas as fases)

- [ ] Campo fechado legível em **claro e escuro** (`.wa-field`/`wa-*`).
- [ ] Popup/dropdown com fundo temizado (não o branco do SO) no dark mode.
- [ ] Busca filtra por digitação; teclado (↑/↓/Enter/Esc) funciona.
- [ ] Clique fora fecha o dropdown.
- [ ] Valor salvo corretamente; reabrir mostra o rótulo certo.
- [ ] Estado "vazio/padrão" preservado onde aplicável (item 1 e 3.1/3.3).
- [ ] Dropdown não estoura containers estreitos (painel lateral — item 3.1).
- [ ] Sem testes de backend afetados (`tests/test_endpoints.py` não cobre frontend; rodar mesmo assim para
      garantir que nada quebrou indiretamente).
</content>
</invoke>
