// Construtor VISUAL de regras — recursivo, com grupos aninhados (plano 76 F8).
// Preact + HTM, sem build step. Legível no modo escuro (classes wa-* e .wa-field).
//
// Estrutura editada (idêntica ao motor):
//   { regras: [ Condicao | Grupo ] }
//   Condicao = { tipo:'condicao', conector?:'E'|'OU', campo, operador, valor }
//   Grupo    = { tipo:'grupo',    conector?:'E'|'OU', regras:[...] }
// O `conector` de um item diz como ele se junta ao item ANTERIOR — o primeiro da lista
// não tem conector (é o acumulador inicial). Grupos são os parênteses.
//
// ⚠️ HTM: NUNCA use crase ou ${...} dentro de comentário em html`...` — fecha o template
// e o módulo quebra em silêncio.
//
// Arrastar e soltar (alça ⠿): a decisão de PARA ONDE o item vai mora em `tree.js`
// (puro, testado); aqui fica só a costura com a API nativa de drag-and-drop do
// navegador — desktop/mouse. Cada item é endereçado por CAMINHO (array de índices),
// e cada faixa de soltura é o par (caminho do pai, índice de inserção).
import { h } from 'preact';
import { useMemo, useRef, useState } from 'preact/hooks';
import htm from 'htm';
import { contarRegras } from './rules.js';
import { mover, podeMover, alturaDe, ehGrupo } from './tree.js';

const html = htm.bind(h);

const VAZIA = { regras: [] };

const chaveCaminho = (caminho) => (caminho || []).join('.');

// ── Helpers de metadata ────────────────────────────────────────────────────

function campoDef(metadata, campoId) {
  return (metadata.campos || []).find((c) => c.id === campoId) || null;
}

function opcoesDe(metadata, campo) {
  if (!campo) return [];
  const src = campo.options;
  if (Array.isArray(src)) return src;
  if (typeof src === 'string') return (metadata.opcoes || {})[src] || [];
  return [];
}

function operadoresDe(metadata, campo) {
  const tipo = (campo && campo.tipo) || 'text';
  const porTipo = metadata.operadores_por_tipo || {};
  return porTipo[tipo] || porTipo.text || ['eq'];
}

function labelOperador(metadata, op) {
  const found = (metadata.operadores || []).find((o) => o.id === op);
  return found ? found.label : op;
}

function usaLista(metadata, op) {
  return (metadata.operadores_lista || []).includes(op);
}

function semValor(metadata, op) {
  return (metadata.operadores_sem_valor || []).includes(op);
}

function camposAgrupados(metadata) {
  const grupos = metadata.grupos || [];
  return grupos.map((g) => ({
    ...g,
    campos: (metadata.campos || []).filter((c) => c.grupo === g.id),
  })).filter((g) => g.campos.length > 0);
}

// Opções do select de campo: os grupos do core, e então um cabeçalho ("Configurações
// (outros plugins)") antes do primeiro grupo contribuído por plugin — <optgroup> não
// aninha, então a seção é uma <option disabled> fazendo as vezes de divisória.
function opcoesDeCampo(metadata) {
  const grupos = camposAgrupados(metadata);
  const rotuloSecao = metadata.grupo_externos_label || 'Configurações (outros plugins)';
  const itens = [];
  grupos.forEach((g, i) => {
    const externo = g.origem === 'plugin';
    const anteriorExterno = i > 0 && grupos[i - 1].origem === 'plugin';
    if (externo && !anteriorExterno) {
      itens.push(html`<option disabled class="font-semibold">${rotuloSecao}</option>`);
    }
    itens.push(html`
      <optgroup label=${g.label}>
        ${g.campos.map((c) => html`<option value=${c.id}>${c.label}</option>`)}
      </optgroup>`);
  });
  return itens;
}

// ── Multi-seleção (operadores de LISTA: in / not_in / has_any / has_all) ───
//
// Chips clicáveis em vez do `<select multiple>` nativo: o nativo exige Ctrl/Cmd+clique
// (não descobrível, e inviável no toque), então o operador de lista parecia aceitar UM
// valor só. Cada clique alterna uma opção; a ordem gravada é a do catálogo (determinística,
// não a ordem dos cliques).
function MultiValorSelect({ opcoes, atual, onChange }) {
  const selecionados = new Set((atual || []).map(String));
  const ordenar = (conj) => opcoes
    .map((o) => String(o.value))
    .filter((v) => conj.has(v));

  const alternar = (v) => {
    const chave = String(v);
    const conj = new Set(selecionados);
    if (conj.has(chave)) conj.delete(chave); else conj.add(chave);
    onChange(ordenar(conj));
  };

  // Largura travada na faixa dos outros inputs de valor (~10rem) para a linha da condição
  // não estourar e jogar o ✕ de remover para a linha de baixo — os chips quebram DENTRO
  // da caixa, que rola quando o campo tem muitas opções.
  return html`
    <div class="rounded border border-wa-border bg-wa-panel px-1.5 py-1 w-[11rem]">
      <div class="flex items-center justify-end gap-1 px-0.5 pb-1">
        <button type="button" title="Selecionar todas as opções"
          onClick=${() => onChange(opcoes.map((o) => String(o.value)))}
          class="text-[10px] text-wa-teal hover:underline">todos</button>
        <span class="text-[10px] text-wa-secondary">·</span>
        <button type="button" title="Limpar a seleção" onClick=${() => onChange([])}
          class="text-[10px] text-wa-secondary hover:underline">limpar</button>
      </div>
      <div class="flex flex-wrap gap-1 max-h-[5.5rem] overflow-y-auto">
        ${opcoes.map((o) => {
          const sel = selecionados.has(String(o.value));
          return html`
            <button type="button" role="checkbox" aria-checked=${sel} title=${o.label}
              onClick=${() => alternar(o.value)}
              class=${`text-[11px] leading-tight rounded-full border px-1.5 py-0.5 max-w-full truncate ${sel
                ? 'bg-wa-teal text-white border-wa-teal'
                : 'bg-wa-hover text-wa-text border-wa-border hover:bg-wa-border'}`}>
              ${sel ? '✓ ' : ''}${o.label}
            </button>`;
        })}
      </div>
    </div>`;
}

// ── Input de valor por tipo ────────────────────────────────────────────────

function ValorInput({ metadata, campo, operador, valor, onChange }) {
  const tipo = (campo && campo.tipo) || 'text';
  if (semValor(metadata, operador)) return null;

  const base = 'wa-field rounded px-2 py-1 text-sm';

  if (operador === 'between') {
    const par = Array.isArray(valor) ? valor : [valor, valor];
    const tipoInput = tipo === 'time' ? 'time' : (tipo === 'date' ? 'date' : 'number');
    const cruza = tipo === 'time' && par[0] && par[1] && String(par[0]) > String(par[1]);
    return html`
      <div class="flex items-center gap-1">
        <input type=${tipoInput} class=${`${base} w-[7.5rem]`} value=${par[0] ?? ''}
          onInput=${(e) => onChange([e.target.value, par[1] ?? ''])} />
        <span class="text-wa-secondary text-xs">e</span>
        <input type=${tipoInput} class=${`${base} w-[7.5rem]`} value=${par[1] ?? ''}
          onInput=${(e) => onChange([par[0] ?? '', e.target.value])} />
        ${cruza ? html`<span class="text-[11px] text-amber-600" title="A faixa atravessa a meia-noite">↩ vira o dia</span>` : null}
      </div>`;
  }

  const opcoes = opcoesDe(metadata, campo);

  if (usaLista(metadata, operador)) {
    const atual = Array.isArray(valor) ? valor.map(String) : (valor ? [String(valor)] : []);
    if (opcoes.length > 0) {
      return html`<${MultiValorSelect} opcoes=${opcoes} atual=${atual} onChange=${onChange} />`;
    }
    return html`
      <input class=${`${base} min-w-[12rem]`} placeholder="valores separados por vírgula"
        value=${atual.join(', ')}
        onInput=${(e) => onChange(e.target.value.split(',').map((s) => s.trim()).filter(Boolean))} />`;
  }

  if (opcoes.length > 0) {
    return html`
      <select class=${`${base} min-w-[10rem]`} value=${valor ?? ''}
        onChange=${(e) => onChange(e.target.value)}>
        <option value="">—</option>
        ${opcoes.map((o) => html`<option value=${o.value}>${o.label}</option>`)}
      </select>`;
  }

  if (tipo === 'time') {
    return html`<input type="time" class=${`${base} w-[7.5rem]`} value=${valor ?? ''}
      onInput=${(e) => onChange(e.target.value)} />`;
  }
  if (tipo === 'date') {
    return html`<input type="date" class=${`${base} w-[9rem]`} value=${valor ?? ''}
      onInput=${(e) => onChange(e.target.value)} />`;
  }
  if (tipo === 'number') {
    return html`<input type="number" step="any" class=${`${base} w-[7rem]`} value=${valor ?? ''}
      onInput=${(e) => onChange(e.target.value)} />`;
  }
  return html`<input class=${`${base} min-w-[10rem]`} value=${valor ?? ''}
    onInput=${(e) => onChange(e.target.value)} />`;
}

// ── Botão E/OU ─────────────────────────────────────────────────────────────

function ConectorBtn({ conector, onChange }) {
  const atual = String(conector || 'E').toUpperCase() === 'OU' ? 'OU' : 'E';
  const cls = atual === 'OU'
    ? 'bg-amber-100 text-amber-700 border-amber-300'
    : 'bg-wa-teal/15 text-wa-teal border-wa-teal/40';
  return html`
    <button type="button" title="Alternar entre E / OU"
      onClick=${() => onChange(atual === 'OU' ? 'E' : 'OU')}
      class=${`shrink-0 w-[3rem] text-xs font-semibold rounded border px-1 py-1 ${cls}`}>
      ${atual}
    </button>`;
}

// ── Arrastar e soltar ──────────────────────────────────────────────────────

// Só a alça é `draggable` — os selects, inputs de hora e chips continuam
// respondendo ao clique normalmente, sem arraste acidental. A imagem arrastada é
// a LINHA inteira (via setDragImage), não o ícone.
function Alca({ dnd, caminho, item, alvoRef, rotulo }) {
  if (!dnd) return null;
  return html`
    <span draggable="true" role="button" title=${rotulo} aria-label=${rotulo}
      onDragStart=${(e) => {
        e.stopPropagation();
        try {
          e.dataTransfer.setData('text/plain', 'retornos:regra');
          e.dataTransfer.effectAllowed = 'move';
          const el = alvoRef && alvoRef.current;
          if (el && e.dataTransfer.setDragImage) {
            e.dataTransfer.setDragImage(el, 16, Math.min(28, el.offsetHeight / 2));
          }
        } catch (err) { /* navegador sem setData/setDragImage — o arraste segue */ }
        dnd.iniciar(caminho, item);
      }}
      onDragEnd=${() => dnd.terminar()}
      class="shrink-0 cursor-grab active:cursor-grabbing select-none px-0.5
             text-wa-secondary hover:text-wa-text leading-none">⠿</span>`;
}

// Faixa de soltura entre dois itens de uma lista. Fica sempre montada (com altura
// mínima) para o DOM não mudar de forma no dragstart — em alguns navegadores isso
// cancela o arraste. Só destaca quando o movimento é aceitável (`tree.podeMover`).
function SoltarAqui({ dnd, pai, indice }) {
  const [sobre, setSobre] = useState(false);
  const arrastando = !!(dnd && dnd.arraste);
  const valido = arrastando && dnd.valido(pai, indice);
  const destacado = valido && sobre;

  let cls = 'h-1';
  if (destacado) cls = 'h-7 my-1 rounded border-2 border-wa-teal bg-wa-teal/25';
  else if (valido) cls = 'h-2 my-0.5 rounded border border-dashed border-wa-teal/40';

  return html`
    <div aria-hidden="true" class=${`transition-all ${cls}`}
      onDragEnter=${(e) => { if (valido) { e.preventDefault(); setSobre(true); } }}
      onDragOver=${(e) => {
        if (!valido) return;
        e.preventDefault();
        e.stopPropagation();
        e.dataTransfer.dropEffect = 'move';
        if (!sobre) setSobre(true);
      }}
      onDragLeave=${() => setSobre(false)}
      onDrop=${(e) => {
        if (!valido) return;
        e.preventDefault();
        e.stopPropagation();
        setSobre(false);
        dnd.soltar(pai, indice);
      }} />`;
}

// ── Linha de condição ──────────────────────────────────────────────────────

function CondicaoRow({ metadata, regra, primeira, onChange, onRemove, dnd, caminho }) {
  const campo = campoDef(metadata, regra.campo);
  const ops = operadoresDe(metadata, campo);
  const operador = ops.includes(regra.operador) ? regra.operador : ops[0];

  function trocarCampo(novoId) {
    const novo = campoDef(metadata, novoId);
    const novosOps = operadoresDe(metadata, novo);
    onChange({ ...regra, campo: novoId, operador: novosOps[0], valor: null });
  }

  const linhaRef = useRef(null);
  const arrastando = dnd && dnd.arrastando(caminho);

  return html`
    <div ref=${linhaRef}
      class=${`flex flex-wrap items-center gap-2 py-1 rounded ${arrastando ? 'opacity-40' : ''}`}>
      <${Alca} dnd=${dnd} caminho=${caminho} item=${regra} alvoRef=${linhaRef}
        rotulo="Arraste para mover esta condição" />
      ${primeira
        ? html`<span class="shrink-0 w-[3rem] text-[11px] text-wa-secondary text-center">se</span>`
        : html`<${ConectorBtn} conector=${regra.conector}
            onChange=${(c) => onChange({ ...regra, conector: c })} />`}

      <select class="wa-field rounded px-2 py-1 text-sm min-w-[13rem]" value=${regra.campo || ''}
        onChange=${(e) => trocarCampo(e.target.value)}>
        <option value="">Escolha o campo…</option>
        ${opcoesDeCampo(metadata)}
      </select>

      <select class="wa-field rounded px-2 py-1 text-sm min-w-[10rem]" value=${operador}
        onChange=${(e) => onChange({ ...regra, operador: e.target.value, valor: null })}>
        ${ops.map((op) => html`<option value=${op}>${labelOperador(metadata, op)}</option>`)}
      </select>

      <${ValorInput} metadata=${metadata} campo=${campo} operador=${operador}
        valor=${regra.valor} onChange=${(v) => onChange({ ...regra, valor: v })} />

      <button type="button" title="Remover condição" onClick=${onRemove}
        class="ml-auto text-red-600 hover:opacity-80 px-1" aria-label="Remover condição">✕</button>
    </div>`;
}

// ── Bloco de grupo (recursivo) ─────────────────────────────────────────────

function GrupoBlock({ metadata, regra, primeira, onChange, onRemove, depth, dnd, caminho }) {
  const { condicoes, grupos } = contarRegras(regra);
  const blocoRef = useRef(null);
  const arrastando = dnd && dnd.arrastando(caminho);

  return html`
    <div ref=${blocoRef}
      class=${`flex items-start gap-2 py-1 rounded ${arrastando ? 'opacity-40' : ''}`}>
      <div class="pt-2 shrink-0">
        <${Alca} dnd=${dnd} caminho=${caminho} item=${regra} alvoRef=${blocoRef}
          rotulo="Arraste para mover este grupo (com o conteúdo)" />
      </div>
      ${primeira
        ? html`<span class="shrink-0 w-[3rem] text-[11px] text-wa-secondary text-center pt-2">se</span>`
        : html`<div class="pt-2"><${ConectorBtn} conector=${regra.conector}
            onChange=${(c) => onChange({ ...regra, conector: c })} /></div>`}
      <div class="flex-1 rounded-lg border border-dashed border-wa-teal/50 bg-wa-teal/5 p-2">
        <div class="flex items-center gap-2 mb-1">
          <span class="text-[11px] uppercase tracking-wide text-wa-secondary">
            Grupo (${condicoes} condição(ões)${grupos ? `, ${grupos} subgrupo(s)` : ''})
          </span>
          <button type="button" title="Remover grupo" onClick=${onRemove}
            class="ml-auto text-red-600 hover:opacity-80 px-1" aria-label="Remover grupo">✕</button>
        </div>
        <${RegrasList} metadata=${metadata} regras=${regra.regras || []} depth=${depth + 1}
          dnd=${dnd} caminho=${caminho}
          onChange=${(regras) => onChange({ ...regra, regras })} />
      </div>
    </div>`;
}

// ── Lista recursiva ────────────────────────────────────────────────────────

function RegrasList({ metadata, regras, onChange, depth = 0, dnd = null, caminho = [] }) {
  const lista = Array.isArray(regras) ? regras : [];

  const trocar = (i, nova) => onChange(lista.map((r, idx) => (idx === i ? nova : r)));
  const remover = (i) => {
    const out = lista.filter((_, idx) => idx !== i);
    if (out.length > 0 && out[0]) delete out[0].conector; // o 1º nunca tem conector
    onChange(out);
  };
  const addCondicao = () => onChange([...lista, {
    tipo: 'condicao', conector: lista.length ? 'E' : undefined,
    campo: '', operador: 'eq', valor: null,
  }]);
  const addGrupo = () => onChange([...lista, {
    tipo: 'grupo', conector: lista.length ? 'E' : undefined, regras: [],
  }]);

  // Item + faixa de soltura ANTES dele; a última faixa (fim da lista) fecha a série.
  // Numa lista vazia sobra só a faixa — é por ela que um grupo recém-criado recebe
  // a primeira condição arrastada de fora.
  const itens = [];
  lista.forEach((regra, i) => {
    itens.push(html`<${SoltarAqui} key=${`z${i}`} dnd=${dnd} pai=${caminho} indice=${i} />`);
    const props = {
      metadata, regra, primeira: i === 0, dnd, caminho: [...caminho, i],
      onChange: (nova) => trocar(i, nova), onRemove: () => remover(i),
    };
    itens.push(ehGrupo(regra)
      ? html`<${GrupoBlock} key=${`i${i}`} ...${props} depth=${depth} />`
      : html`<${CondicaoRow} key=${`i${i}`} ...${props} />`);
  });
  itens.push(html`<${SoltarAqui} key="zfim" dnd=${dnd} pai=${caminho} indice=${lista.length} />`);

  return html`
    <div>
      ${lista.length === 0
        ? html`<div class="text-xs text-wa-secondary py-1">
            Sem condições — <strong>sempre passa</strong>.
          </div>`
        : null}
      ${itens}

      <div class="flex gap-2 mt-1">
        <button type="button" onClick=${addCondicao}
          class="text-xs px-2 py-1 rounded bg-wa-hover text-wa-text hover:bg-wa-border">
          + Condição
        </button>
        ${depth < 5 ? html`
          <button type="button" onClick=${addGrupo}
            class="text-xs px-2 py-1 rounded bg-wa-hover text-wa-text hover:bg-wa-border">
            + Grupo
          </button>` : null}
      </div>
    </div>`;
}

// ── Componente público ─────────────────────────────────────────────────────

export function RegraBuilder({ metadata, arvore, onChange, titulo = 'Regras',
                               ajuda = '', preview = null }) {
  const [aberto, setAberto] = useState(true);
  const [arraste, setArraste] = useState(null);
  const valor = (arvore && typeof arvore === 'object' && Array.isArray(arvore.regras))
    ? arvore : VAZIA;
  const resumo = useMemo(() => contarRegras(valor), [valor]);

  // O arraste é resolvido na RAIZ: só aqui existe a árvore inteira, e um item pode
  // sair de um grupo e cair em outro (a lista local de cada nível não bastaria).
  const dnd = {
    arraste,
    iniciar: (caminho, item) => setArraste({
      caminho, chave: chaveCaminho(caminho), altura: alturaDe(item),
    }),
    terminar: () => setArraste(null),
    arrastando: (caminho) => !!arraste && arraste.chave === chaveCaminho(caminho),
    valido: (pai, indice) => !!arraste && podeMover(valor.regras, arraste.caminho, pai, indice),
    soltar: (pai, indice) => {
      if (!arraste) return;
      const novas = mover(valor.regras, arraste.caminho, pai, indice);
      setArraste(null);
      if (novas) onChange({ regras: novas });
    },
  };

  return html`
    <section class="rounded-lg border border-wa-border bg-wa-bg p-3">
      <div class="flex items-center gap-2">
        <button type="button" onClick=${() => setAberto(!aberto)}
          class="text-sm font-semibold text-wa-text flex items-center gap-1">
          <span class="text-wa-secondary">${aberto ? '▾' : '▸'}</span> ${titulo}
        </button>
        <span class="text-[11px] text-wa-secondary">
          ${resumo.condicoes === 0
            ? 'sem condições (sempre passa)'
            : `${resumo.condicoes} condição(ões)${resumo.grupos ? ` · ${resumo.grupos} grupo(s)` : ''}`}
        </span>
        ${preview !== null && preview !== undefined ? html`
          <span class=${`ml-auto text-[11px] px-2 py-0.5 rounded-full ${
            preview ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
            ${preview ? 'passaria agora' : 'não passaria agora'}
          </span>` : null}
      </div>
      ${ajuda ? html`<p class="text-[11px] text-wa-secondary mt-1">${ajuda}</p>` : null}
      ${aberto ? html`
        <div class="mt-2">
          <p class="text-[11px] text-wa-secondary mb-1">
            Arraste pela alça <span class="text-wa-text">⠿</span> para reordenar, tirar de um
            grupo ou soltar dentro de outro — o <strong>E/OU</strong> acompanha o item, e quem
            fica em 1º na lista vira o <strong>se</strong>.
          </p>
          <${RegrasList} metadata=${metadata} regras=${valor.regras} dnd=${dnd} caminho=${[]}
            onChange=${(regras) => onChange({ regras })} />
        </div>` : null}
    </section>`;
}

export default RegraBuilder;
