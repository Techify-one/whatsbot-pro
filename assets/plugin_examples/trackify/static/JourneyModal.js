// Modal "Jornada do cliente" — o que o contato fez no Trackify (o CDP do Nexus).
//
// Três blocos: Identidade · Assinaturas · Linha do tempo. Sem build step
// (Preact + HTM). Legível no modo escuro: só classes wa-* e .wa-field.
//
// Estados de PRIMEIRA CLASSE, não casos de erro — o casamento por telefone acerta
// ~43% (medido em produção), então "sem cadastro" é o desfecho MAIS COMUM e
// precisa de uma saída útil (busca manual por e-mail/CPF), não de uma tela vazia:
//   carregando · não configurado · sem cadastro (+busca) · ambíguo (seletor) · jornada
import { h } from 'preact';
import { useState, useEffect, useCallback } from 'preact/hooks';
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

function Stat({ label, value, strong }) {
  return html`
    <div>
      <div class="text-[11px] text-wa-secondary">${label}</div>
      <div class=${`text-sm ${strong ? 'font-semibold' : ''} text-wa-text`}>${value ?? '—'}</div>
    </div>`;
}

function IdentityBlock({ ident }) {
  const status = STATUS_LABEL[ident.status] || ident.status;
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

      ${ident.identifiers && ident.identifiers.length ? html`
        <div class="grid grid-cols-2 sm:grid-cols-3 gap-3 mt-4 pt-3 border-t border-wa-border">
          ${ident.identifiers.map((f) => html`
            <${Stat} key=${f.slug} label=${f.name} value=${f.value} />`)}
          ${(ident.fields || []).filter((f) => f.slug !== 'name').map((f) => html`
            <${Stat} key=${f.slug} label=${f.name} value=${f.value} />`)}
        </div>` : null}
    </section>`;
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
        <div class="grid grid-cols-2 sm:grid-cols-3 gap-2 mt-2 pt-2 border-t border-wa-border">
          ${rest.map((k) => html`
            <${Stat} key=${k} label=${FIELD_LABEL[k] || k} value=${String(fields[k])} />`)}
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

export function JourneyModal({ api, contactId, onClose }) {
  const [state, setState] = useState({ loading: true });
  const [filter, setFilter] = useState('');
  const [loadingMore, setLoadingMore] = useState(false);

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
  let body;
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
    body = html`
      <div class="space-y-4">
        <${IdentityBlock} ident=${{ ...data.identity, events_total: (data.event_types || [])
          .reduce((a, t) => a + Number(t.total || 0), 0) }} />
        <${SubscriptionsBlock} subs=${data.subscriptions} />

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
      <div class="w-full max-w-3xl my-6 bg-wa-bg rounded-2xl shadow-2xl">
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
        <div class="p-5">${body}</div>
      </div>
    </div>`;
}
