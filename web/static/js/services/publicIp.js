// @ts-check
//
// IP público do navegador, informado pelo painel (plano 86).
//
// Por quê: na instalação real o IP de origem morre num hop ANTES do proxy
// reverso — toda request chega ao app com o mesmo `X-Forwarded-For` privado, e
// nenhum código do backend consegue recuperar o endereço do operador (medido:
// LAN, internet pública e o painel real chegam idênticos). Como o dado nunca
// entra no processo, quem tem de informá-lo é o próprio navegador.
//
// Este módulo descobre o IP público UMA vez por carregamento de página e o
// guarda em memória; `authHeaders()` (services/httpClient.js) o injeta no
// cabeçalho `X-Client-Public-IP` de toda chamada à API — core e plugins.
//
// ⚠️ O valor é AUTODECLARADO e portanto forjável (decisão D1 do plano, aceita):
// alimenta APENAS a coluna `ip_address` da trilha de auditoria. O rate-limit de
// login continua no IP observado na rede (D4) — se dependesse deste valor,
// bastaria variá-lo a cada tentativa para anular o limite.
//
// Falha (offline, CSP, bloqueio de rede, timeout) degrada em SILÊNCIO: o
// cabeçalho simplesmente não vai, e o backend cai no IP de rede de hoje.

/** Fonte: texto simples `chave=valor` por linha, sem chave e sem cadastro. */
const TRACE_URL = 'https://www.cloudflare.com/cdn-cgi/trace';

/** Teto da consulta — o painel nunca espera por ela, mas nada fica pendurado. */
const TIMEOUT_MS = 3000;

/** @type {string} valor descoberto nesta carga da página ('' = desconhecido). */
let publicIp = '';

/** @type {Promise<string>|null} guarda de idempotência da busca em voo. */
let inflight = null;

/**
 * Extrai o `ip=` de um corpo no formato `chave=valor` por linha.
 * PURO — é o que os testes cobrem.
 *
 * @param {string} text - corpo bruto do endpoint de trace.
 * @returns {string|null} o IP declarado, ou `null` para corpo inesperado.
 */
export function parseTrace(text) {
  if (typeof text !== 'string') return null;
  for (const line of text.split('\n')) {
    const eq = line.indexOf('=');
    if (eq < 0) continue;
    if (line.slice(0, eq).trim() !== 'ip') continue;
    const value = line.slice(eq + 1).trim();
    return value || null;
  }
  return null;
}

/**
 * Dispara a descoberta (fire-and-forget). Idempotente: a segunda chamada
 * reaproveita a primeira e nunca refaz a consulta.
 *
 * @returns {Promise<string>} o IP descoberto, ou `''` em qualquer falha.
 */
export function initPublicIp() {
  if (publicIp) return Promise.resolve(publicIp);
  if (inflight) return inflight;

  inflight = (async () => {
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
      try {
        const res = await fetch(TRACE_URL, {
          method: 'GET',
          cache: 'no-store',
          credentials: 'omit',
          signal: ctrl.signal,
        });
        if (!res.ok) return '';
        publicIp = parseTrace(await res.text()) || '';
        return publicIp;
      } finally {
        clearTimeout(timer);
      }
    } catch (_) {
      return '';                    // offline / CSP / abort — sem ruído no console
    }
  })();

  return inflight;
}

/**
 * @returns {string} o IP público desta carga da página, ou `''` se ainda não
 *   chegou (ou se a consulta falhou). Nunca lança.
 */
export function getPublicIp() {
  return publicIp;
}
