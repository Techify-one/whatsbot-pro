// Espelho JS do motor de regras (`rules.py`) — porte 1:1, usado no PREVIEW da UI.
// PURO: sem preact, sem HTM, sem fetch — testável com `node --test rules.test.js`.
//
// Regras do porte (as mesmas travadas no plano 76):
//  * lista de regras avaliada da ESQUERDA para a DIREITA, primeira regra ignora `conector`
//  * `grupo` recursa e vira UM booleano (parênteses) — profundidade arbitrária (D4)
//  * `between` em campo de HORA atravessa a meia-noite quando min > max (D7)
//  * árvore vazia => true; campo desconhecido => condição false
//
// Mudou um operador aqui? Mude em `rules.py` também — os dois usam os mesmos vetores de teste.

export const CTX_NOW = '__now';
export const CTX_TZ = '__tz_offset_hours';
export const CTX_LAST_CLIENT = '__last_client_ts';

// Espelho mínimo do catálogo (só o que o motor precisa: tipo + temporal).
// Mantido aqui para o módulo ser autossuficiente no preview; a UI usa o /metadata
// do backend para rótulos e opções.
export const TIPOS = {
  'chatwoot.agente_id': 'select',
  'chatwoot.status': 'enum',
  'chatwoot.etiquetas': 'multi-select',
  'contato.tags': 'multi-select',
  'chatwoot.criado_em': 'date',
  'chatwoot.ultima_atividade': 'date',
  'wb.canal': 'select',
  'wb.contact_type': 'enum',
  'wb.agente_ia': 'select',
  'wb.ia_ativa': 'enum',
  'wb.grupo': 'enum',
  'virtual.hora_atual': 'time',
  'virtual.data_atual': 'date',
  'virtual.dia_semana': 'enum',
  'modulo.n_disparos_enviados': 'number',
  'modulo.hora_ultimo_contato': 'time',
  'modulo.horas_desde_ultimo_contato': 'number',
  'modulo.dias_desde_ultimo_contato': 'number',
  'modulo.dias_calendario_desde_contato': 'number',
  'modulo.minutos_desde_ultimo_contato': 'number',
};

export const TEMPORAIS = new Set([
  'virtual.hora_atual', 'virtual.data_atual',
  'modulo.minutos_desde_ultimo_contato', 'modulo.horas_desde_ultimo_contato',
  'modulo.dias_desde_ultimo_contato', 'modulo.dias_calendario_desde_contato',
]);

/**
 * Mapa `campoId -> tipo` a partir do /metadata do backend — inclui os campos que
 * OUTROS plugins contribuíram, que o espelho estático `TIPOS` não conhece. Sem esse
 * merge o preview trataria um campo de plugin como desconhecido (= sempre falso).
 */
export function tiposDeMetadata(metadata) {
  const out = { ...TIPOS };
  ((metadata && metadata.campos) || []).forEach((c) => {
    if (c && c.id) out[String(c.id)] = c.tipo || 'text';
  });
  return out;
}

/** Tipo de um campo (default 'text'); `null` quando o campo é desconhecido. */
export function tipoDe(campoId, tipos = TIPOS) {
  const t = tipos[String(campoId || '')];
  return t === undefined ? null : t;
}

// ── Avaliação ──────────────────────────────────────────────────────────────

export function avaliar(arvore, ctx, tipos = TIPOS) {
  if (!arvore || typeof arvore !== 'object') return true;
  return avaliarRegras(arvore.regras || [], ctx, tipos);
}

export function avaliarRegras(regras, ctx, tipos = TIPOS) {
  if (!Array.isArray(regras) || regras.length === 0) return true;
  let acc = null;
  for (const regra of regras) {
    let valor;
    try {
      valor = avaliarRegra(regra, ctx, tipos);
    } catch {
      valor = false;
    }
    if (acc === null) { acc = valor; continue; }
    const conector = String((regra && regra.conector) || 'E').toUpperCase();
    acc = (conector === 'OU' || conector === 'OR') ? (acc || valor) : (acc && valor);
  }
  return !!acc;
}

function avaliarRegra(regra, ctx, tipos) {
  if (!regra || typeof regra !== 'object') return false;
  if (String(regra.tipo || '').toLowerCase() === 'grupo') {
    return avaliarRegras(regra.regras || [], ctx, tipos);
  }
  return avaliarCondicao(regra, ctx, tipos);
}

export function avaliarCondicao(cond, ctx, tipos = TIPOS) {
  const campoId = String((cond && cond.campo) || '');
  const tipo = tipoDe(campoId, tipos);
  if (tipo === null) return false; // campo desconhecido => falso
  const operador = String((cond && cond.operador) || '').toLowerCase();
  return aplicarOperador(operador, (ctx || {})[campoId], cond ? cond.valor : undefined,
    tipo, ctx || {});
}

// ── Operadores ─────────────────────────────────────────────────────────────

export function aplicarOperador(operador, atual, referencia, tipo, ctx = {}) {
  if (operador === 'exists') return preenchido(atual);
  if (operador === 'not_exists') return !preenchido(atual);

  if (operador === 'has_any' || operador === 'has_all'
      || operador === 'in' || operador === 'not_in') {
    const listaAtual = asList(atual);
    const listaRef = asList(referencia);
    if (operador === 'has_any') return listaRef.some((v) => listaAtual.includes(v));
    if (operador === 'has_all') {
      return listaRef.length > 0 && listaRef.every((v) => listaAtual.includes(v));
    }
    if (operador === 'in') return listaRef.includes(normStr(atual));
    return !listaRef.includes(normStr(atual)); // not_in
  }

  if (operador === 'contains' || operador === 'not_contains'
      || operador === 'starts_with' || operador === 'ends_with') {
    const a = normStr(atual);
    const b = normStr(referencia);
    if (operador === 'contains') return !!b && a.includes(b);
    if (operador === 'not_contains') return !(!!b && a.includes(b));
    if (operador === 'starts_with') return !!b && a.startsWith(b);
    return !!b && a.endsWith(b);
  }

  if (operador === 'between') {
    const [minimo, maximo] = par(referencia);
    const a = coerce(atual, tipo, ctx);
    const lo = coerce(minimo, tipo, ctx);
    const hi = coerce(maximo, tipo, ctx);
    if (a === null || lo === null || hi === null) return false;
    if (tipo === 'time' && lo > hi) return a >= lo || a <= hi; // D7 — cruza a meia-noite
    return lo <= a && a <= hi;
  }

  if (operador === 'gt' || operador === 'gte' || operador === 'lt' || operador === 'lte') {
    const a = coerce(atual, tipo, ctx);
    const b = coerce(referencia, tipo, ctx);
    if (a === null || b === null) return false;
    if (operador === 'gt') return a > b;
    if (operador === 'gte') return a >= b;
    if (operador === 'lt') return a < b;
    return a <= b;
  }

  if (operador === 'eq' || operador === 'neq') {
    const igual = iguais(atual, referencia, tipo, ctx);
    return operador === 'eq' ? igual : !igual;
  }

  return false; // operador desconhecido => falso
}

function iguais(atual, referencia, tipo, ctx) {
  if (tipo === 'number' || tipo === 'time' || tipo === 'date') {
    const a = coerce(atual, tipo, ctx);
    const b = coerce(referencia, tipo, ctx);
    if (a === null || b === null) return false;
    return a === b;
  }
  if (tipo === 'multi-select') return asList(atual).includes(normStr(referencia));
  return normStr(atual) === normStr(referencia);
}

// ── Normalização ───────────────────────────────────────────────────────────

function preenchido(v) {
  if (v === null || v === undefined) return false;
  if (typeof v === 'string') return v.trim() !== '';
  if (Array.isArray(v)) return v.length > 0;
  if (typeof v === 'object') return Object.keys(v).length > 0;
  return true;
}

export function normStr(v) {
  if (v === null || v === undefined) return '';
  if (typeof v === 'boolean') return v ? 'sim' : 'nao';
  return String(v).trim().toLowerCase();
}

export function asList(v) {
  if (v === null || v === undefined) return [];
  if (Array.isArray(v)) return v.map(normStr).filter((s) => s !== '');
  const s = normStr(v);
  if (s === '') return [];
  if (s.includes(',')) return s.split(',').map((p) => p.trim()).filter(Boolean);
  return [s];
}

function par(referencia) {
  if (referencia && typeof referencia === 'object' && !Array.isArray(referencia)) {
    return [referencia.min, referencia.max];
  }
  if (Array.isArray(referencia)) return [referencia[0], referencia[1]];
  if (typeof referencia === 'string' && referencia.includes(',')) {
    const [a, b] = referencia.split(',');
    return [a.trim(), b.trim()];
  }
  return [referencia, referencia];
}

function coerce(v, tipo, ctx) {
  if (tipo === 'time') return parseHhmm(v);
  if (tipo === 'date') return toEpoch(v, tz(ctx));
  return toFloat(v);
}

export function toFloat(v) {
  if (v === null || v === undefined || typeof v === 'boolean') return null;
  if (typeof v === 'number') return Number.isNaN(v) ? null : v;
  const s = String(v).trim().replace(',', '.');
  if (s === '') return null;
  const n = Number(s);
  return Number.isNaN(n) ? null : n;
}

export function parseHhmm(v) {
  if (v === null || v === undefined) return null;
  if (typeof v === 'number') return Math.trunc(v);
  const s = String(v).trim();
  if (s === '') return null;
  if (s.includes(':')) {
    const parts = s.split(':');
    const hh = Number(parts[0]);
    const mm = Number(parts[1]);
    if (!Number.isFinite(hh) || !Number.isFinite(mm)) return null;
    if (hh < 0 || hh > 23 || mm < 0 || mm > 59) return null;
    return hh * 60 + mm;
  }
  const n = Number(s);
  return Number.isFinite(n) ? Math.trunc(n) : null;
}

export function toEpoch(v, tzHours) {
  if (v === null || v === undefined) return null;
  if (typeof v === 'number') return v;
  const s = String(v).trim();
  if (s === '') return null;
  if (/^\d{9,}(\.\d+)?$/.test(s)) return Number(s);
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?/);
  if (!m) return toFloat(s);
  const [, y, mo, d, hh, mi, ss] = m;
  const utc = Date.UTC(Number(y), Number(mo) - 1, Number(d),
    Number(hh || 0), Number(mi || 0), Number(ss || 0)) / 1000;
  return utc - tzHours * 3600;
}

function tz(ctx) {
  const v = Number((ctx || {})[CTX_TZ]);
  return Number.isFinite(v) ? v : -3;
}

/** Instante "local" da configuração (offset fixo) como Date em UTC. */
export function localDate(epoch, tzHours) {
  return new Date((Number(epoch) + tzHours * 3600) * 1000);
}

export function epochOfLocalMidnight(epoch, tzHours, plusDays = 0) {
  const d = localDate(epoch, tzHours);
  const midnight = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(),
    d.getUTCDate() + plusDays) / 1000;
  return midnight - tzHours * 3600;
}

export function epochOfLocalTime(epoch, tzHours, minutesOfDay, plusDays = 0) {
  return epochOfLocalMidnight(epoch, tzHours, plusDays) + minutesOfDay * 60;
}

// ── Hint de agendamento ────────────────────────────────────────────────────

export function proximoInstante(arvore, ctx, now = null, fallbackSeconds = 60) {
  const agora = Number(now !== null ? now : ((ctx || {})[CTX_NOW] || 0));
  const tzh = tz(ctx);
  const last = (ctx || {})[CTX_LAST_CLIENT];
  const out = [];
  coletar(arvore && typeof arvore === 'object' ? arvore : {}, agora, tzh, last, out);
  const futuros = out.filter((c) => c !== null && c !== undefined && c > agora);
  if (futuros.length === 0) return agora + fallbackSeconds;
  return Math.min(Math.min(...futuros), agora + 86400 * 30);
}

function coletar(no, agora, tzh, last, out) {
  for (const regra of (no.regras || [])) {
    if (!regra || typeof regra !== 'object') continue;
    if (String(regra.tipo || '').toLowerCase() === 'grupo') {
      coletar(regra, agora, tzh, last, out);
      continue;
    }
    const campoId = String(regra.campo || '');
    if (!TEMPORAIS.has(campoId)) continue;
    let operador = String(regra.operador || '').toLowerCase();
    let valor = regra.valor;
    if (operador === 'between') { valor = par(valor)[0]; operador = 'gte'; }
    const cand = candidato(campoId, operador, valor, agora, tzh, last);
    if (cand !== null && cand !== undefined) out.push(cand);
  }
}

function candidato(campoId, operador, valor, agora, tzh, last) {
  if (campoId === 'virtual.hora_atual') {
    const alvo = parseHhmm(valor);
    if (alvo === null) return null;
    if (operador === 'gte' || operador === 'gt' || operador === 'eq') {
      const hoje = epochOfLocalTime(agora, tzh, alvo);
      return hoje > agora ? hoje : epochOfLocalTime(agora, tzh, alvo, 1);
    }
    if (operador === 'lte' || operador === 'lt') {
      return epochOfLocalMidnight(agora, tzh, 1);
    }
    return null;
  }
  if (campoId === 'virtual.data_atual') {
    const alvo = toEpoch(valor, tzh);
    if (alvo === null) return null;
    if (operador === 'gte' || operador === 'gt' || operador === 'eq') return alvo;
    return null;
  }
  if (last === null || last === undefined) return null;
  const fatores = {
    'modulo.minutos_desde_ultimo_contato': 60,
    'modulo.horas_desde_ultimo_contato': 3600,
    'modulo.dias_desde_ultimo_contato': 86400,
  };
  if (fatores[campoId] && (operador === 'gte' || operador === 'gt' || operador === 'eq')) {
    const n = toFloat(valor);
    if (n === null) return null;
    return Number(last) + n * fatores[campoId];
  }
  if (campoId === 'modulo.dias_calendario_desde_contato'
      && (operador === 'gte' || operador === 'gt' || operador === 'eq')) {
    const n = toFloat(valor);
    if (n === null) return null;
    return epochOfLocalMidnight(Number(last), tzh, Math.trunc(n));
  }
  return null;
}

/** Conta condições e grupos de uma árvore (usado no resumo da UI). */
export function contarRegras(arvore) {
  let condicoes = 0;
  let grupos = 0;
  const walk = (no) => {
    for (const regra of ((no && no.regras) || [])) {
      if (!regra || typeof regra !== 'object') continue;
      if (String(regra.tipo || '').toLowerCase() === 'grupo') { grupos += 1; walk(regra); }
      else condicoes += 1;
    }
  };
  walk(arvore || {});
  return { condicoes, grupos };
}

export default { avaliar, avaliarRegras, avaliarCondicao, aplicarOperador, proximoInstante, contarRegras };
