// @ts-check
//
// Cliente HTTP da tela "Arquivos" (plano 179). Funções finas sobre o transporte
// comum (`httpClient.js`): o envelope `{ok, data, error}` e o tratamento de 401/403
// são os mesmos do resto do painel. A prévia é a exceção — devolve bytes, não JSON —
// então usa `fetch` direto com `authHeaders()` (mesmo seam de cabeçalho).

import { request, authHeaders, handleUnauthorized } from './httpClient.js';

/** @param {Record<string, string | number | boolean | null | undefined>} params */
function queryString(params) {
  const sp = new URLSearchParams();
  for (const [key, value] of Object.entries(params || {})) {
    if (value === undefined || value === null || value === '') continue;
    sp.set(key, String(value));
  }
  const s = sp.toString();
  return s ? `?${s}` : '';
}

/** Uso do disco + (para quem vê tudo) o que cada pasta ocupa. */
export function getFilesUsage(refresh = false) {
  return request('GET', `/api/files/usage${queryString({ refresh: refresh || undefined })}`);
}

/** Contatos/grupos que `q` nomeia, para o seletor "Contato ou grupo". */
export function getFileContacts(q, limit = 8) {
  return request('GET', `/api/files/contacts${queryString({ q, limit })}`);
}

/** Uma página de arquivos (ver `buildListParams` em fileManagerView.js). */
export function listFiles(params) {
  return request('GET', `/api/files${queryString(params)}`);
}

/** Apaga (até 1000 ids por chamada). */
export function deleteFiles(ids) {
  return request('POST', '/api/files/delete', { ids });
}

/**
 * Canais para o filtro "Canal": `[{id, name}]`, já recortado pelo escopo da pessoa.
 * Não usa `/api/inboxes` (gateada por `inbox.manage`, que quem só tem `files.manage`
 * não tem). `silent` mantém o filtro como melhoria opcional: se falhar, a tela só
 * o esconde, sem aviso de permissão.
 */
export function listInboxes() {
  return request('GET', '/api/files/inboxes', undefined, { silent: true });
}

/**
 * Bytes do arquivo pelo endpoint autenticado. Quem chama decide o que fazer com o
 * `Response` (a prévia vira blob). 401 derruba a sessão como no resto do painel.
 * @param {string} id id opaco devolvido pela listagem
 * @param {{download?: boolean, signal?: AbortSignal | null}} [opts]
 * @returns {Promise<Response>}
 */
export async function fetchFilePreview(id, { download = false, signal = null } = {}) {
  const url = `/api/files/${encodeURIComponent(id)}/preview${queryString({ download: download || undefined })}`;
  /** @type {RequestInit} */
  const init = { headers: authHeaders() };
  if (signal) init.signal = signal;
  const res = await fetch(url, init);
  if (res.status === 401) handleUnauthorized();
  return res;
}
