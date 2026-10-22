// @ts-check
//
// Tela "Arquivos" (plano 179) — lógica PURA: formatação de bytes/datas, nível de
// aviso do disco, resumo do que a confirmação vai fazer, soma dos lotes de exclusão
// e a montagem da query de listagem. Sem preact, sem DOM e sem rede, para rodar com
// `node --test web/static/js/services/fileManagerView.test.js`.
//
// O servidor devolve o que é de cada arquivo (`plugin`, `orphan`, `origin`); aqui só
// se decide como MOSTRAR e o que dizer antes/depois de apagar.

/** Tamanho da página da lista e do "Mostrar mais". */
export const PAGE = 100;
/** "Selecionar tudo o que o filtro achou" caminha nas páginas do teto do servidor. */
export const SELECT_PAGE = 500;
/** Ids por chamada de exclusão (teto do servidor). */
export const DELETE_BATCH = 1000;
/** Texto puro é cortado nesse tamanho, para um log enorme não travar a aba. */
export const TEXT_PREVIEW_BYTES = 256 * 1024;

/** Disco a partir do qual a barra vira amarela / vermelha (em %). */
export const DISK_WARN_PCT = 80;
export const DISK_DANGER_PCT = 95;

export const DISK_WARNING_TEXT =
  'O disco do servidor está quase cheio. Apague arquivos que não precisa mais ou aumente o disco na hospedagem.';

/** Dica do botão desabilitado: arquivo de plugin sem nenhuma conversa. */
export const PLUGIN_ONLY_TITLE = 'Para apagar, remova o arquivo no próprio plugin';

/**
 * `1536` → `"1,5 KB"`. Vai até TB; casas decimais caem conforme o número cresce.
 * @param {number | null | undefined} n
 */
export function fmtBytes(n) {
  if (!n || n < 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  const digits = v >= 100 || i === 0 ? 0 : v >= 10 ? 1 : 2;
  return `${v.toFixed(digits).replace('.', ',')} ${units[i]}`;
}

/** `plural(1, 'arquivo', 'arquivos')` → `"1 arquivo"`. */
export function plural(n, one, many) {
  return `${Number(n || 0).toLocaleString('pt-BR')} ${Number(n) === 1 ? one : many}`;
}

/** @param {number | null | undefined} ts epoch em segundos */
export function fmtDateTime(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const date = d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric' });
  const time = d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
  return `${date} ${time}`;
}

/** `<input type="date">` fala "YYYY-MM-DD" no calendário da própria pessoa. */
export function isoDate(d) {
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/**
 * Meia-noite LOCAL (do navegador) do dia escolhido, em epoch segundos. `addDays = 1`
 * faz do último dia escolhido o fim EXCLUSIVO do intervalo, para o dia inteiro contar.
 * Devolve `''` para uma data inválida/vazia (o campo fica fora da query).
 * @param {string} iso "YYYY-MM-DD"
 * @param {number} [addDays]
 * @returns {number | ''}
 */
export function dayStart(iso, addDays = 0) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
  if (!m) return '';
  return new Date(+m[1], +m[2] - 1, +m[3] + addDays).getTime() / 1000;
}

/**
 * De/até de um período pronto ("Hoje" = 1 dia, "Últimos 7 dias" = 7…), em "YYYY-MM-DD".
 * @param {number} days
 * @param {Date} [now]
 */
export function periodRange(days, now = new Date()) {
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  start.setDate(start.getDate() - (days - 1));
  return { from: isoDate(start), to: isoDate(now) };
}

// ── Disco ─────────────────────────────────────────────────────────────────

/**
 * % usada do disco (0–100), ou `null` quando o servidor não soube medir.
 * @param {{total?: number, used?: number} | null | undefined} disk
 */
export function diskPercent(disk) {
  if (!disk || !disk.total || disk.total <= 0) return null;
  return Math.max(0, Math.min(100, ((disk.used || 0) * 100) / disk.total));
}

/** @returns {'ok' | 'warn' | 'danger'} */
export function diskLevel(pct) {
  if (pct == null) return 'ok';
  if (pct >= DISK_DANGER_PCT) return 'danger';
  if (pct >= DISK_WARN_PCT) return 'warn';
  return 'ok';
}

// ── Prévia ────────────────────────────────────────────────────────────────

/**
 * Quem decide o que é o arquivo é o servidor (extensão, depois os primeiros bytes), e
 * ele diz no Content-Type — a prévia segue isso em vez de adivinhar pelo nome.
 * @returns {'image' | 'video' | 'audio' | 'pdf' | 'text' | 'other'}
 */
export function previewFamily(mime) {
  const m = String(mime || '').split(';')[0].trim().toLowerCase();
  if (m.startsWith('image/')) return 'image';
  if (m.startsWith('video/')) return 'video';
  if (m.startsWith('audio/')) return 'audio';
  if (m === 'application/pdf') return 'pdf';
  if (m.startsWith('text/')) return 'text';
  return 'other';
}

// ── Linha da lista ────────────────────────────────────────────────────────

/**
 * Link para a mensagem exata do arquivo. `null` quando a mensagem é legada e não tem
 * conversa (mostra só o nome, sem link).
 * @param {{conversation_id?: number | null, message_id?: number | null} | null | undefined} origin
 */
export function conversationHref(origin) {
  if (!origin || origin.conversation_id == null) return null;
  const base = `/conversations/${origin.conversation_id}`;
  return origin.message_id != null ? `${base}?message=${origin.message_id}` : base;
}

/**
 * Arquivo de plugin que nenhuma conversa usa mais: apagar daqui não faria nada (o
 * arquivo físico é do plugin), então a ação some e o title explica.
 */
export function canDelete(item) {
  return !(item && item.plugin && item.orphan);
}

// ── Exclusão ──────────────────────────────────────────────────────────────

/**
 * O que a confirmação precisa saber ANTES de apagar. Arquivo de plugin só sai das
 * conversas; só o resto sai do disco.
 * @param {Array<{id: string, size?: number, plugin?: object | null}>} list
 */
export function summarizeSelection(list) {
  const out = {
    ids: list.map((i) => i.id),
    total: list.length,
    disk: { count: 0, bytes: 0 },
    plugin: { count: 0, bytes: 0 },
  };
  for (const item of list) {
    const bucket = item.plugin ? out.plugin : out.disk;
    bucket.count += 1;
    bucket.bytes += item.size || 0;
  }
  return out;
}

/**
 * Texto da janela de confirmação.
 * @param {ReturnType<typeof summarizeSelection>} s
 * @returns {{title: string, lines: string[]}}
 */
export function confirmCopy(s) {
  const title = s.total === 1 ? 'Apagar este arquivo?' : `Apagar ${plural(s.total, 'arquivo', 'arquivos')}?`;
  const lines = [];
  if (s.disk.count > 0) {
    lines.push(`${plural(s.disk.count, 'arquivo será apagado', 'arquivos serão apagados')} do servidor (${fmtBytes(s.disk.bytes)}). Isso não pode ser desfeito.`);
  } else {
    lines.push('Nenhum arquivo será apagado do servidor.');
  }
  if (s.plugin.count > 0) {
    lines.push(s.plugin.count === 1
      ? '1 é usado por plugin: sai das conversas, mas continua no servidor para o plugin enviar.'
      : `${s.plugin.count.toLocaleString('pt-BR')} são usados por plugins: saem das conversas, mas continuam no servidor para o plugin enviar.`);
  }
  lines.push('As conversas continuam. As mensagens ligadas a esses arquivos passam a mostrar “Arquivo removido”.');
  return { title, lines };
}

/** Zera o acumulador dos lotes de exclusão. */
export function emptyDeleteResult() {
  return { files: 0, bytes: 0, failed: 0, messages: 0, kept_plugin: 0, kept_unknown: 0 };
}

/**
 * Soma a resposta de UM lote (`POST /api/files/delete`) no acumulador. Campo ausente
 * conta zero (um servidor mais antigo não manda `kept_*`).
 */
export function addDeleteResult(acc, data) {
  for (const key of Object.keys(acc)) acc[key] += Number((data && data[key]) || 0);
  return acc;
}

/**
 * Aviso final com os números REAIS devolvidos pelo servidor.
 * @param {ReturnType<typeof emptyDeleteResult>} r
 * @param {string} [error] mensagem de falha no meio dos lotes (o que já saiu é reportado)
 * @returns {{kind: 'success' | 'error', text: string}}
 */
export function deleteNotice(r, error = '') {
  const parts = [];
  parts.push(r.files > 0
    ? `${plural(r.files, 'arquivo apagado', 'arquivos apagados')} · ${fmtBytes(r.bytes)} liberados`
    : 'Nenhum arquivo foi apagado do servidor');
  if (r.kept_plugin > 0) {
    parts.push(`${plural(r.kept_plugin, 'arquivo de plugin saiu das conversas mas continua', 'arquivos de plugin saíram das conversas mas continuam')} no servidor`);
  }
  if (r.kept_unknown > 0) {
    parts.push(`${plural(r.kept_unknown, 'arquivo continua', 'arquivos continuam')} no servidor porque não foi possível confirmar que nenhum plugin o usa`);
  }
  if (r.failed > 0) parts.push(`${plural(r.failed, 'não pôde ser apagado', 'não puderam ser apagados')}`);
  const summary = `${parts.join(' · ')}.`;
  if (!error) return { kind: 'success', text: summary };
  const done = r.files + r.kept_plugin + r.kept_unknown > 0;
  return { kind: 'error', text: done ? `${error} Antes disso, ${summary.charAt(0).toLowerCase()}${summary.slice(1)}` : error };
}

// ── Query da listagem ─────────────────────────────────────────────────────

/**
 * Parâmetros de `GET /api/files` a partir do estado dos filtros. Só entra o que está
 * preenchido (um filtro vazio não polui a URL). `contact_id` (escolhido no seletor)
 * vence o texto digitado em `contact`.
 * @param {{root: string, sort: string, order: string, kind?: string, orphans?: boolean,
 *   q?: string, contact?: string, contactId?: number | string, inboxId?: number | string,
 *   chatType?: string, dateFrom?: string, dateTo?: string}} f
 * @param {number} offset
 * @param {number} limit
 */
export function buildListParams(f, offset, limit) {
  /** @type {Record<string, string | number | boolean>} */
  const p = { root: f.root, sort: f.sort, order: f.order, offset, limit };
  if (f.kind) p.kind = f.kind;
  if (f.orphans) p.orphans = true;
  if (f.q) p.q = f.q;
  if (f.contactId) p.contact_id = f.contactId;
  else if (f.contact) p.contact = f.contact;
  if (f.inboxId) p.inbox_id = f.inboxId;
  if (f.chatType) p.chat_type = f.chatType;
  const from = dayStart(f.dateFrom || '');
  const to = dayStart(f.dateTo || '', 1);
  if (from !== '') p.date_from = from;
  if (to !== '') p.date_to = to;
  return p;
}

/** Há algum filtro ligado? (decide "Limpar filtros" e o sentido do "selecionar todos"). */
export function hasActiveFilters(f) {
  return !!(f.kind || f.orphans || f.q || f.contact || f.contactId || f.inboxId
    || f.chatType || f.dateFrom || f.dateTo);
}
