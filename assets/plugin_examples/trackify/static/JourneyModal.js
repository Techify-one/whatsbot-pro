// Modal "Jornada do cliente" — o que o contato fez no Trackify (o CDP do Nexus).
//
// Duas abas: *Jornada* (Identidade + Linha do tempo) e *Produtos* (compras +
// Assinaturas). Empilhar os quatro blocos numa coluna só obrigava a rolar por
// cima do cadastro inteiro para achar um produto. Sem build step (Preact +
// HTM). Legível no modo escuro: só classes wa-* e .wa-field.
//
// Estados de PRIMEIRA CLASSE, não casos de erro — o casamento por telefone acerta
// ~43% (medido em produção), então "sem cadastro" é o desfecho MAIS COMUM e
// precisa de uma saída útil (busca manual por e-mail/CPF), não de uma tela vazia:
//   carregando · não configurado · sem cadastro (+busca) · ambíguo (seletor) · jornada
import { h } from 'preact';
import { useState, useEffect, useCallback, useMemo } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

// ── Rótulos ──────────────────────────────────────────────────────────────
// O CDP guarda event_type cru (vem dos webhooks de Ticto/Pagar.me/etc). O
// vendedor não tem que decorar esse vocabulário.
const EVENT_LABEL = {
  purchase: 'Compra', authorized: 'Pagamento autorizado', refused: 'Pagamento recusado',
  refunded: 'Reembolso', chargeback: 'Chargeback', claimed: 'Reclamação',
  pix_created: 'PIX gerado', pix_expired: 'PIX expirado',
  bank_slip_created: 'Boleto gerado', bank_slip_delayed: 'Boleto atrasado',
  waiting_payment: 'Aguardando pagamento', card_exchanged: 'Cartão trocado',
  active_subscription: 'Assinatura ativa', subscription_canceled: 'Assinatura cancelada',
  subscription_delayed: 'Assinatura atrasada',
  initiate_checkout: 'Iniciou checkout', payment_method: 'Escolheu forma de pagamento',
  pix_generated: 'PIX gerado', coupon_applied: 'Cupom aplicado',
  lead_email: 'Informou e-mail', lead_document: 'Informou documento',
  lead_whatsapp: 'Informou WhatsApp', lead_nome: 'Informou nome',
  disparo_whatsapp: 'Disparo de WhatsApp', importacao_lista: 'Importado de lista',
  'charge.paid': 'Cobrança paga', 'order.paid': 'Pedido pago',
  'order.payment_failed': 'Pagamento falhou', 'charge.payment_failed': 'Cobrança falhou',
  'charge.antifraud_reproved': 'Reprovado no antifraude',
  'charge.refunded': 'Cobrança reembolsada', 'order.canceled': 'Pedido cancelado',
};

const STATUS_LABEL = { lead: 'Lead', customer: 'Cliente', inactive: 'Inativo' };

// Positivo (verde) / negativo (vermelho) / neutro. Guia o olho do vendedor.
const NEGATIVE = new Set(['refused', 'refunded', 'chargeback', 'claimed', 'pix_expired',
  'subscription_canceled', 'subscription_delayed', 'bank_slip_delayed',
  'order.payment_failed', 'charge.payment_failed', 'charge.antifraud_reproved',
  'charge.refunded', 'order.canceled']);
const POSITIVE = new Set(['purchase', 'authorized', 'active_subscription',
  'charge.paid', 'order.paid']);

// Campos dinâmicos que valem destaque; o resto vai no "ver detalhes".
const FIELD_LABEL = {
  product_name: 'Produto', offer_name: 'Oferta', payment_method: 'Pagamento',
  installments: 'Parcelas', status: 'Situação', transaction_id: 'Transação',
  utm_source: 'Origem', utm_medium: 'Mídia', utm_campaign: 'Campanha',
  utm_content: 'Conteúdo', utm_term: 'Termo', card_brand: 'Bandeira',
  card_last_digits: 'Final do cartão', subscription_interval: 'Periodicidade',
  next_charge_date: 'Próxima cobrança', successful_charges: 'Cobranças pagas',
  failed_charges: 'Cobranças falhas', coupon: 'Cupom',
};
const HIGHLIGHT = ['product_name', 'offer_name', 'payment_method', 'status'];

function eventLabel(t) { return EVENT_LABEL[t] || t; }

function fmtDate(iso, withTime = true) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d)) return String(iso);
  const opts = withTime
    ? { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }
    : { day: '2-digit', month: '2-digit', year: 'numeric' };
  return d.toLocaleString('pt-BR', opts);
}

function daysLabel(n) {
  if (n == null) return null;
  if (n < 0) return `venceu há ${Math.abs(n)} dia${Math.abs(n) === 1 ? '' : 's'}`;
  if (n === 0) return 'é hoje';
  return `faltam ${n} dia${n === 1 ? '' : 's'}`;
}

// ── Peças ────────────────────────────────────────────────────────────────

function Chip({ children, tone = 'neutral', title }) {
  const cls = tone === 'good' ? 'bg-wa-teal/15 text-wa-teal'
    : tone === 'bad' ? 'bg-red-100 text-red-700'
    : 'bg-wa-hover text-wa-secondary';
  return html`<span title=${title}
    class="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium ${cls}">${children}</span>`;
}

// `min-w-0` é obrigatório: item de grade nasce com `min-width:auto` (= min-content),
// então um token sem espaço — hash de transação, URL de utm_content, blob de JSON —
// alarga a coluna até estourar o painel. Foi exatamente o que quebrou o layout de
// "ver detalhes". `break-words` faz o token quebrar em vez de empurrar.
function Stat({ label, value, strong }) {
  const v = value === null || value === undefined || value === '' ? '—' : value;
  return html`
    <div class="min-w-0">
      <div class="text-[11px] text-wa-secondary truncate" title=${label}>${label}</div>
      <div class=${`text-sm break-words ${strong ? 'font-semibold' : ''} text-wa-text`}>${v}</div>
    </div>`;
}

function IdentityBlock({ ident }) {
  const status = STATUS_LABEL[ident.status] || ident.status;
  // `name` sai da grade: já é o título do bloco.
  const identificadores = (ident.identifiers || []).filter((f) => f.slug !== 'name');
  // Antes esta lista era renderizada ANINHADA no `if (identifiers.length)`, então
  // um contato sem nenhum identificador preenchido perdia também o cadastro
  // inteiro. São blocos independentes.
  const demais = (ident.fields || []).filter((f) => f.slug !== 'name');
  return html`
    <section class="border border-wa-border rounded-xl p-4 bg-wa-panel">
      <div class="flex items-start justify-between gap-3 flex-wrap">
        <div class="min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            <h3 class="text-base font-semibold text-wa-text truncate">${ident.name || 'Sem nome'}</h3>
            <${Chip} tone=${ident.status === 'customer' ? 'good' : 'neutral'}>${status}<//>
          </div>
          ${ident.tags && ident.tags.length ? html`
            <div class="flex gap-1 flex-wrap mt-1.5">
              ${ident.tags.map((t) => html`<${Chip} key=${t.name}>${t.name}<//>`)}
            </div>` : null}
        </div>
        ${ident.link ? html`
          <a href=${ident.link} target="_blank" rel="noopener noreferrer"
            class="text-[12px] px-2.5 py-1 rounded-md bg-wa-teal/15 text-wa-teal hover:bg-wa-teal/25 transition-colors whitespace-nowrap">
            Abrir no Trackify ↗
          </a>` : null}
      </div>

      <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-4">
        <${Stat} label="Total gasto" value=${ident.total_spent || 'R$ 0,00'} strong=${true} />
        <${Stat} label="Primeiro contato" value=${fmtDate(ident.first_seen_at, false)} />
        <${Stat} label="Virou cliente em" value=${ident.converted_at ? fmtDate(ident.converted_at, false) : '—'} />
        <${Stat} label="Eventos" value=${ident.events_total ?? '—'} />
      </div>

      ${identificadores.length ? html`
        <div class="mt-4 pt-3 border-t border-wa-border">
          <div class="text-[11px] uppercase tracking-wide text-wa-secondary mb-2">Identificadores</div>
          <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
            ${identificadores.map((f) => html`
              <${Stat} key=${f.slug} label=${f.name} value=${f.value} />`)}
          </div>
        </div>` : null}

      ${demais.length ? html`
        <div class="mt-4 pt-3 border-t border-wa-border">
          <div class="text-[11px] uppercase tracking-wide text-wa-secondary mb-2">Informações do contato</div>
          <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
            ${demais.map((f) => html`
              <${Stat} key=${f.slug} label=${f.name} value=${f.value} />`)}
          </div>
        </div>` : null}
    </section>`;
}

// O que o cliente COMPROU. O Trackify não tem tabela de produto — a linha é
// derivada dos eventos, e a regra mora no backend (``journey.fetch_purchases``):
// compra é o que a ``channel_value_rules`` do CDP diz que é. Se o contato
// comprou alguma vez, ele aparece — o selo ROTULA o último estado, nunca
// decide se a linha existe.
function PurchasesBlock({ purchases }) {
  const bloco = purchases || {};
  const items = bloco.items || [];
  const unruled = bloco.unruled || [];
  const unnamed = bloco.unnamed || [];

  if (bloco.unavailable) {
    return html`
      <section class="py-8 text-center">
        <div class="text-sm text-wa-text">Não foi possível carregar os produtos agora.</div>
        <div class="text-[12px] text-wa-secondary mt-1">A aba Jornada continua disponível.</div>
      </section>`;
  }

  // Dois diagnósticos IRMÃOS e opostos, fáceis de confundir. Ambos apontam para
  // a configuração do CDP, não para um bug do plugin — e mandar o operador
  // configurar o canal errado é pior que não avisar nada.
  const origens = (lista) => lista
    .map((u) => `${u.channel} · ${eventLabel(u.event_type)}`).join(', ');
  const total = (lista) => lista.reduce((a, u) => a + Number(u.events || 0), 0);

  const avisos = [];
  if (unruled.length) {
    // A compra existe, mas o Trackify não a classifica como dinheiro.
    avisos.push(html`
      <div key="unruled" class="text-[12px] text-wa-secondary">
        ${total(unruled)} evento(s) com produto vieram de ${origens(unruled)},
        que não têm regra de valor no Trackify — compras assim não aparecem aqui.
      </div>`);
  }
  if (unnamed.length) {
    // O oposto: é dinheiro reconhecido, mas o evento não diz O QUÊ foi comprado.
    avisos.push(html`
      <div key="unnamed" class="text-[12px] text-wa-secondary">
        ${total(unnamed)} cobrança(s) de ${origens(unnamed)} entraram no total gasto,
        mas não trazem nome nem id de produto — não dá para listá-las como linha.
      </div>`);
  }

  if (!items.length) {
    return html`
      <section class="py-8 text-center">
        <div class="text-sm text-wa-text">
          ${avisos.length ? 'Nenhuma compra pôde ser listada.' : 'Este contato ainda não comprou nada.'}
        </div>
        ${avisos.length ? html`
          <div class="max-w-md mx-auto mt-2 space-y-2">${avisos}</div>` : null}
      </section>`;
  }

  return html`
    <section>
      <h4 class="text-[13px] font-semibold text-wa-text mb-2">Compras (${items.length})</h4>
      <ul class="space-y-2">
        ${items.map((p) => html`<${PurchaseRow} key=${p.key} p=${p} />`)}
      </ul>
      ${avisos.length ? html`<div class="mt-3 space-y-2">${avisos}</div>` : null}
    </section>`;
}

function PurchaseRow({ p }) {
  // O selo é informativo. ``last_effect`` vem da regra de valor do CDP e cobre
  // tipo desconhecido de canal novo (sem entrada no EVENT_LABEL); ``NEGATIVE``
  // cobre o estado que a regra marcou "ignore" (cancelamento, atraso).
  const tone = p.last_effect === 'add' ? 'good'
    : p.last_effect === 'subtract' ? 'bad'
    : NEGATIVE.has(p.last_event_type) ? 'bad' : 'neutral';

  return html`
    <li class="border border-wa-border rounded-lg p-3 bg-wa-panel min-w-0">
      <div class="flex items-start justify-between gap-3 flex-wrap">
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="text-sm font-semibold text-wa-text break-words">${p.name}</span>
            <${Chip} tone=${tone} title=${p.last_event_type}>${eventLabel(p.last_event_type)}<//>
            ${p.interval ? html`<${Chip}>${p.interval}<//>` : null}
          </div>
          ${p.offer && p.offer !== p.name ? html`
            <div class="text-[12px] text-wa-secondary mt-0.5 break-words">${p.offer}</div>` : null}
        </div>
        <div class="text-right shrink-0">
          <div class="text-sm font-semibold text-wa-text">${p.paid_total || 'R$ 0,00'}</div>
          <div class="text-[11px] text-wa-secondary">
            ${p.purchases} compra${p.purchases === 1 ? '' : 's'}
          </div>
        </div>
      </div>

      <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-3 pt-2 border-t border-wa-border">
        <${Stat} label="Primeira compra" value=${p.first_purchase_at ? fmtDate(p.first_purchase_at, false) : ''} />
        <${Stat} label="Última compra" value=${p.last_purchase_at ? fmtDate(p.last_purchase_at, false) : ''} />
        <${Stat} label="Pagamento" value=${p.payment_method} />
        <${Stat} label="Situação no gateway" value=${p.gateway_status} />
        ${p.refunded ? html`<${Stat} label="Reembolsado" value=${p.refunded} />` : null}
      </div>
    </li>`;
}

// Strip de abas LOCAL, espelhando o de `config.js`. Importar de lá arrastaria a
// tela de configuração inteira (~24 KB) para dentro do modal da conversa, e não
// existe componente de aba compartilhado em lugar nenhum do repo — duplicar 12
// linhas é o preço certo. Corrigido o que o `config.js` deixou passar:
// `type="button"` explícito e `key=` nos botões mapeados.
const JOURNEY_TABS = [['jornada', 'Jornada'], ['produtos', 'Produtos']];

function JourneyTabs({ value, onChange, count }) {
  return html`
    <nav class="px-5 pt-3 flex gap-1 border-b border-wa-border overflow-x-auto">
      ${JOURNEY_TABS.map(([id, label]) => html`
        <button key=${id} type="button" onClick=${() => onChange(id)}
          class=${`px-4 py-2 text-sm -mb-px border-b-2 transition-colors whitespace-nowrap ${
            value === id ? 'border-wa-teal text-wa-text'
              : 'border-transparent text-wa-secondary hover:text-wa-text'}`}>
          ${id === 'produtos' ? `${label} (${count || 0})` : label}
        </button>`)}
    </nav>`;
}

function SubscriptionsBlock({ subs }) {
  if (!subs || !subs.length) return null;
  return html`
    <section>
      <h4 class="text-[13px] font-semibold text-wa-text mb-2">Assinaturas</h4>
      <div class="space-y-2">
        ${subs.map((s) => {
          const dl = daysLabel(s.days_left);
          return html`
            <div key=${s.key} class="border border-wa-border rounded-xl p-3 bg-wa-panel">
              <div class="flex items-start justify-between gap-3 flex-wrap">
                <div class="min-w-0">
                  <div class="text-sm font-medium text-wa-text truncate">${s.product || s.key}</div>
                  ${s.offer && s.offer !== s.product ? html`
                    <div class="text-[12px] text-wa-secondary truncate">${s.offer}</div>` : null}
                </div>
                ${s.canceled_at
                  ? html`<${Chip} tone="bad">Cancelada em ${fmtDate(s.canceled_at, false)}<//>`
                  : html`<${Chip} tone="good">${s.status || 'Ativa'}<//>`}
              </div>
              <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-3">
                <${Stat} label="Próxima cobrança"
                  value=${s.next_charge ? fmtDate(s.next_charge, false) : (s.next_charge_raw || '—')} />
                <${Stat} label="Prazo" value=${dl || '—'} />
                <${Stat} label="Pagas / falhas"
                  value=${`${s.successful_charges ?? '—'} / ${s.failed_charges ?? '—'}`} />
                <${Stat} label="Último valor" value=${s.last_value || '—'} />
              </div>
            </div>`;
        })}
      </div>
    </section>`;
}

// Um campo do "ver detalhes". Três formatos, porque os valores do CDP não têm
// um formato só: `wb_raw` é um JSON de centenas de caracteres, `transaction_id`
// é um hash sem espaço e `installments` é "3". Tratar os três igual foi o que
// fez o conteúdo escapar do painel.
const LONG_VALUE = 60;

function DetailField({ slug, value }) {
  const text = value === null || value === undefined ? '' : String(value);
  const json = useMemo(() => {
    const t = text.trim();
    if (!(t.startsWith('{') || t.startsWith('['))) return null;
    try { return JSON.stringify(JSON.parse(t), null, 2); } catch (_) { return null; }
  }, [text]);

  if (json !== null) {
    // JSON ganha a linha inteira e rolagem PRÓPRIA: quebrar um blob no meio de
    // uma chave o torna ilegível, e deixá-lo crescer arrasta o painel junto.
    return html`
      <div class="min-w-0 sm:col-span-2">
        <div class="text-[11px] text-wa-secondary">${FIELD_LABEL[slug] || slug}</div>
        <pre class="text-[11px] text-wa-text bg-wa-hover rounded p-2 mt-0.5 max-h-48 overflow-auto whitespace-pre">${json}</pre>
      </div>`;
  }

  const wide = text.length > LONG_VALUE;
  return html`
    <div class=${`min-w-0 ${wide ? 'sm:col-span-2' : ''}`}>
      <div class="text-[11px] text-wa-secondary">${FIELD_LABEL[slug] || slug}</div>
      <div class="text-sm text-wa-text break-words">${text || '—'}</div>
    </div>`;
}

function EventRow({ ev }) {
  const [open, setOpen] = useState(false);
  const fields = ev.fields || {};
  const keys = Object.keys(fields);
  const tone = NEGATIVE.has(ev.event_type) ? 'bad' : POSITIVE.has(ev.event_type) ? 'good' : 'neutral';
  const highlights = HIGHLIGHT.filter((k) => fields[k]);
  const rest = keys.filter((k) => !HIGHLIGHT.includes(k));

  return html`
    <li class="border border-wa-border rounded-lg p-3 bg-wa-panel">
      <div class="flex items-start justify-between gap-3 flex-wrap">
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-2 flex-wrap">
            <${Chip} tone=${tone} title=${ev.event_type}>${eventLabel(ev.event_type)}<//>
            ${ev.channel ? html`<span class="text-[11px] text-wa-secondary">${ev.channel}</span>` : null}
          </div>
          <div class="text-sm text-wa-text mt-1 break-words">${ev.title}</div>
          ${highlights.length ? html`
            <div class="text-[12px] text-wa-secondary mt-0.5 break-words">
              ${highlights.map((k) => `${FIELD_LABEL[k] || k}: ${fields[k]}`).join(' · ')}
            </div>` : null}
        </div>
        <div class="text-right shrink-0">
          <div class="text-sm font-semibold text-wa-text">${ev.value || '—'}</div>
          <div class="text-[11px] text-wa-secondary">${fmtDate(ev.occurred_at)}</div>
        </div>
      </div>

      ${rest.length ? html`
        <button type="button" onClick=${() => setOpen(!open)}
          class="text-[11px] text-wa-teal hover:underline mt-2">
          ${open ? 'ocultar detalhes' : `ver detalhes (${rest.length})`}
        </button>` : null}
      ${open ? html`
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-2 mt-2 pt-2 border-t border-wa-border">
          ${rest.map((k) => html`<${DetailField} key=${k} slug=${k} value=${fields[k]} />`)}
        </div>` : null}
    </li>`;
}

// ── Estados vazios ───────────────────────────────────────────────────────

function ManualSearch({ api, onFound }) {
  const [email, setEmail] = useState('');
  const [cpf, setCpf] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  async function run(e) {
    e.preventDefault();
    if (!email.trim() && !cpf.trim()) return;
    setBusy(true); setMsg('');
    try {
      const qs = new URLSearchParams();
      if (email.trim()) qs.set('email', email.trim());
      if (cpf.trim()) qs.set('cpf', cpf.trim());
      const r = await api.http.get(`/journey/search?${qs}`);
      if (r && r.ok && r.data && (r.data.found || r.data.ambiguous)) onFound(r.data);
      else setMsg('Nenhum cadastro com esse e-mail ou CPF.');
    } catch (_) {
      setMsg('Não foi possível buscar agora.');
    } finally { setBusy(false); }
  }

  return html`
    <form onSubmit=${run} class="mt-4 pt-4 border-t border-wa-border">
      <div class="text-[12px] text-wa-secondary mb-2">
        Procurar por outro identificador:
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <input class="wa-field px-2.5 py-1.5 text-sm rounded-md" type="email"
          placeholder="e-mail do cliente" value=${email}
          onInput=${(e) => setEmail(e.target.value)} />
        <input class="wa-field px-2.5 py-1.5 text-sm rounded-md" type="text"
          placeholder="CPF (com ou sem pontos)" value=${cpf}
          onInput=${(e) => setCpf(e.target.value)} />
      </div>
      <div class="flex items-center gap-3 mt-2">
        <button type="submit" disabled=${busy}
          class="px-3 py-1.5 rounded-md text-[12px] bg-wa-teal/15 text-wa-teal hover:bg-wa-teal/25 disabled:opacity-50 transition-colors">
          ${busy ? 'Procurando…' : 'Procurar'}
        </button>
        ${msg ? html`<span class="text-[12px] text-wa-secondary">${msg}</span>` : null}
      </div>
    </form>`;
}

function CandidatePicker({ candidates, onPick }) {
  return html`
    <div class="space-y-2">
      <div class="text-sm text-wa-text">
        ${candidates.length} cadastros casaram com este contato. Qual é o certo?
      </div>
      ${candidates.map((c) => html`
        <button key=${c.contact_id} type="button" onClick=${() => onPick(c.contact_id)}
          class="w-full text-left border border-wa-border rounded-lg p-3 bg-wa-panel hover:bg-wa-hover transition-colors">
          <div class="text-sm font-medium text-wa-text">${c.name || 'Sem nome'}</div>
          <div class="text-[12px] text-wa-secondary">
            ${(c.identifiers || []).map((i) => i.value).join(' · ')}
          </div>
          <div class="text-[12px] text-wa-secondary mt-1">
            Total gasto ${c.total_spent || 'R$ 0,00'} · desde ${fmtDate(c.first_seen_at, false)}
          </div>
        </button>`)}
    </div>`;
}

// ── Modal ────────────────────────────────────────────────────────────────

// Largura: max-w-5xl (1024px). Com o cadastro completo, os produtos e a linha do
// tempo, 3xl (768px) espremia tudo em duas colunas e estourava com valor longo.
// O `min-w-0` no painel é o que impede um filho de grade empurrar a largura para
// fora da tela — sem ele, `max-w` não segura nada.
//
// Altura: o painel NÃO tem restrição de altura (quem rola é o backdrop), então
// sem um piso a troca para a aba Produtos — quase sempre mais curta — encolhe o
// painel e deixa o usuário olhando para espaço vazio. Daí o `min-h-[45vh]`.
export function JourneyModal({ api, contactId, onClose }) {
  const [state, setState] = useState({ loading: true });
  const [filter, setFilter] = useState('');
  const [loadingMore, setLoadingMore] = useState(false);
  // Sem persistência (precedente: o `ViewEditorModal` dos protocolos) e sem
  // reset ao recarregar: quem estava em "Produtos" e escolheu outro cadastro no
  // `CandidatePicker` volta já em "Produtos".
  const [tab, setTab] = useState('jornada');

  const load = useCallback(async () => {
    setState({ loading: true });
    if (contactId == null) {
      setState({ loading: false, error: 'Não foi possível identificar o contato desta conversa.' });
      return;
    }
    try {
      const r = await api.http.get(`/journey?contact_id=${encodeURIComponent(contactId)}`);
      if (!r || r.ok === false) {
        setState({ loading: false, error: (r && r.error) || 'Falha ao consultar o Trackify.' });
        return;
      }
      setState({ loading: false, data: r.data });
    } catch (_) {
      setState({ loading: false, error: 'Falha ao consultar o Trackify.' });
    }
  }, [api, contactId]);

  useEffect(() => { load(); }, [load]);

  const data = state.data || {};
  const tId = data.identity && data.identity.contact_id;

  // Troca de filtro por tipo → recarrega a 1ª página do servidor (a paginação
  // é do banco; filtrar só o que já veio mentiria na contagem).
  const applyFilter = useCallback(async (type) => {
    setFilter(type);
    if (!tId) return;
    setLoadingMore(true);
    try {
      const qs = new URLSearchParams({ trackify_contact_id: tId });
      if (type) qs.set('event_type', type);
      const r = await api.http.get(`/journey/events?${qs}`);
      if (r && r.ok) {
        setState((s) => ({ ...s, data: { ...s.data, timeline: r.data } }));
      }
    } finally { setLoadingMore(false); }
  }, [api, tId]);

  const loadMore = useCallback(async () => {
    const tl = data.timeline;
    if (!tId || !tl) return;
    setLoadingMore(true);
    try {
      const qs = new URLSearchParams({
        trackify_contact_id: tId,
        offset: String(tl.offset + tl.events.length),
      });
      if (filter) qs.set('event_type', filter);
      const r = await api.http.get(`/journey/events?${qs}`);
      if (r && r.ok) {
        setState((s) => ({
          ...s,
          data: {
            ...s.data,
            timeline: { ...r.data, events: [...tl.events, ...r.data.events], offset: tl.offset },
          },
        }));
      }
    } finally { setLoadingMore(false); }
  }, [api, tId, data.timeline, filter]);

  const pickCandidate = useCallback(async (id) => {
    setState({ loading: true });
    try {
      const r = await api.http.get(`/journey/by-id?trackify_contact_id=${encodeURIComponent(id)}`);
      setState({ loading: false, data: r && r.ok ? r.data : undefined,
                 error: r && r.ok ? undefined : 'Falha ao abrir o cadastro.' });
    } catch (_) {
      setState({ loading: false, error: 'Falha ao abrir o cadastro.' });
    }
  }, [api]);

  // ── corpo ──
  // A cadeia de ramos continua ÚNICA: os seis primeiros (carregando, erro, não
  // configurado, grupo, ambíguo, sem cadastro) ficam intactos e deixam
  // `tabStrip` em `null` — nesses estados o modal renderiza exatamente como
  // antes, sem aba nenhuma. Só o ramo terminal da jornada completa preenche.
  let body;
  let tabStrip = null;
  if (state.loading) {
    body = html`<div class="py-12 text-center text-sm text-wa-secondary">Consultando o Trackify…</div>`;
  } else if (state.error) {
    body = html`<div class="py-10 text-center text-sm text-wa-secondary">${state.error}</div>`;
  } else if (data.configured === false) {
    body = html`
      <div class="py-10 text-center">
        <div class="text-sm text-wa-text">Conexão com o Trackify não configurada.</div>
        <div class="text-[12px] text-wa-secondary mt-1">
          Um administrador precisa informar o DSN do Nexus em Plugins → Trackify → Configurar.
        </div>
      </div>`;
  } else if (data.is_group) {
    body = html`
      <div class="py-10 text-center text-sm text-wa-secondary">
        Conversas de grupo não têm jornada — o Trackify guarda pessoas, não grupos.
      </div>`;
  } else if (data.ambiguous) {
    body = html`<${CandidatePicker} candidates=${data.candidates || []} onPick=${pickCandidate} />`;
  } else if (!data.found) {
    body = html`
      <div class="py-6 text-center">
        <div class="text-sm text-wa-text">Nenhum cadastro no Trackify para este contato.</div>
        <div class="text-[12px] text-wa-secondary mt-1">
          O número do WhatsApp não casou com nenhum contato do CDP. Isso é comum:
          o cliente pode ter comprado com outro número.
        </div>
        <div class="max-w-md mx-auto text-left">
          <${ManualSearch} api=${api} onFound=${(d) => setState({ loading: false, data: d })} />
        </div>
      </div>`;
  } else {
    const tl = data.timeline || { events: [], total: 0 };
    const shown = tl.events.length;
    const compras = (data.purchases && data.purchases.items) || [];
    tabStrip = html`<${JourneyTabs} value=${tab} onChange=${setTab} count=${compras.length} />`;
    // A aba inativa é DESMONTADA (o que todo strip do repo faz). Consequência
    // aceita: voltar para a Jornada colapsa o "ver detalhes" de cada `EventRow`
    // (esse `open` é `useState` local). Não se perde o filtro nem as páginas já
    // carregadas da timeline — moram em `state.data.timeline`, aqui no
    // componente raiz, então trocar de aba não dispara fetch nenhum. Não "conserte"
    // subindo o `open` para cá.
    body = tab === 'produtos' ? html`
      <div class="space-y-4">
        <${PurchasesBlock} purchases=${data.purchases} />
        <${SubscriptionsBlock} subs=${data.subscriptions} />
      </div>`
    : html`
      <div class="space-y-4">
        <${IdentityBlock} ident=${{ ...data.identity, events_total: (data.event_types || [])
          .reduce((a, t) => a + Number(t.total || 0), 0) }} />

        <section>
          <div class="flex items-center justify-between gap-3 flex-wrap mb-2">
            <h4 class="text-[13px] font-semibold text-wa-text">
              Linha do tempo ${tl.total ? html`<span class="text-wa-secondary font-normal">(${tl.total})</span>` : null}
            </h4>
            ${(data.event_types || []).length > 1 ? html`
              <select class="wa-field px-2 py-1 text-[12px] rounded-md"
                value=${filter} onChange=${(e) => applyFilter(e.target.value)}>
                <option value="">Todos os tipos</option>
                ${data.event_types.map((t) => html`
                  <option key=${t.event_type} value=${t.event_type}>
                    ${eventLabel(t.event_type)} (${t.total})
                  </option>`)}
              </select>` : null}
          </div>

          ${shown === 0 ? html`
            <div class="text-sm text-wa-secondary py-6 text-center">Nenhum evento neste filtro.</div>
          ` : html`
            <ul class="space-y-2">
              ${tl.events.map((ev) => html`<${EventRow} key=${ev.id} ev=${ev} />`)}
            </ul>`}

          ${shown < tl.total ? html`
            <div class="text-center mt-3">
              <button type="button" onClick=${loadMore} disabled=${loadingMore}
                class="px-3 py-1.5 rounded-md text-[12px] bg-wa-hover text-wa-text hover:bg-wa-border disabled:opacity-50 transition-colors">
                ${loadingMore ? 'Carregando…' : `Carregar mais (${tl.total - shown} restantes)`}
              </button>
            </div>` : null}
        </section>
      </div>`;
  }

  const who = (data.whatsbot && data.whatsbot.name) || '';

  return html`
    <div class="fixed inset-0 z-[70] flex items-start justify-center bg-black/50 p-4 overflow-y-auto"
      onClick=${(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div class="w-full max-w-5xl min-w-0 my-6 bg-wa-bg rounded-2xl shadow-2xl">
        <header class="flex items-center justify-between gap-3 px-5 py-4 border-b border-wa-border">
          <div class="min-w-0">
            <h2 class="text-base font-semibold text-wa-text">Jornada do cliente</h2>
            <div class="text-[12px] text-wa-secondary truncate">
              ${who ? `${who} · ` : ''}Trackify${data.matched_by ? ` · casou por ${data.matched_by}` : ''}
            </div>
          </div>
          <button type="button" onClick=${onClose} aria-label="Fechar"
            class="px-2 py-1 rounded-md text-wa-secondary hover:text-wa-text hover:bg-wa-hover transition-colors">✕</button>
        </header>
        ${tabStrip}
        <div class="p-5 min-h-[45vh]">${body}</div>
      </div>
    </div>`;
}
