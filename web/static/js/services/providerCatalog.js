// @ts-check
//
// Provider CATALOG (plano 76 · H1) — a ÚNICA fonte de "rótulo, cor e tipo de
// contato" de um provider de canal no cliente. Antes do plano 76 cada tela que
// precisava disso copiava um mapa estático (ChannelChip, ConversationInfoPanel,
// ChannelPickerModal, NewConversationModal, contactTypes) — cinco cópias que
// desatualizavam a cada provider novo. Agora todas leem daqui, e daqui vem do
// DESCRIPTOR que o backend serve em `GET /api/channels/providers` (o mesmo que a
// tela Canais já consome). Zero nome de provider hardcoded no core.
//
// Contrato (D3, aditivo): antes do fetch resolver (ou se ele falhar — endpoint
// autenticado, tela aberta antes do login) responde o FALLBACK estático mínimo
// (só os bundled: gowa/test) e, para qualquer provider desconhecido, degrada para
// o próprio identificador em cinza. Uma instalação sem plugin de canal nenhum
// continua renderizando — nada quebra.
//
// Mutação/re-render no molde do registry de plugins (plugins/registry.js): um
// fetch preguiçoso na 1ª leitura, cache em módulo, `subscribe()`/`getVersion()`
// que bumpam quando os descriptors chegam (async) para as telas re-renderizarem.

import { listChannelProviders } from './api.js';
import { tintForColor } from '../components/channels/constants.js';

// Fallback estático mínimo — só os providers que EXISTEM sem plugin importável
// (gowa é bundled; test é o provider fictício). Cobre o intervalo entre o mount e
// a chegada do fetch, e o caso de o endpoint estar indisponível. Cor no formato
// de token do descriptor (resolvida por tintForColor), consistente com o resto.
const FALLBACK = {
  gowa: { label: 'GOWA', color: 'green', contact_type: 'whatsapp' },
  test: { label: 'Teste', color: 'gray', contact_type: 'outros' },
};

/** @type {Record<string, any> | null} */
let _descriptors = null;         // provider -> descriptor (null até o 1º fetch)
/** @type {Record<string, string[]>} */
let _required = {};              // provider -> [credential keys required]
let _loading = false;
let _version = 0;
const _subscribers = new Set();

function bump() {
  _version++;
  for (const fn of _subscribers) { try { fn(_version); } catch (_) { /* ignore */ } }
}

/** Subscribe to catalog updates; returns an unsubscribe fn (registry molde). */
export function subscribe(fn) { _subscribers.add(fn); return () => _subscribers.delete(fn); }
export function getVersion() { return _version; }

// Dispara o fetch único (idempotente). Chamado preguiçosamente na 1ª leitura de
// qualquer getter. Falha silenciosa: mantém o fallback e permite um novo retry na
// próxima leitura (não trava o cache num estado ruim).
function ensureLoaded() {
  if (_descriptors !== null || _loading) return;
  _loading = true;
  (async () => {
    try {
      const res = await listChannelProviders();
      if (res && res.ok && res.data) {
        const map = {};
        for (const d of (res.data.providers || [])) if (d && d.provider) map[d.provider] = d;
        _descriptors = map;
        _required = res.data.required_credentials || {};
        bump();
      }
    } catch (_) { /* mantém fallback; retry na próxima leitura */ }
    finally { _loading = false; }
  })();
}

/**
 * Descriptor resolvido de um provider: o servido pelo backend quando já chegou,
 * senão o fallback estático mínimo, senão null (provider desconhecido).
 * @param {string} provider
 * @returns {any|null}
 */
export function descriptorFor(provider) {
  ensureLoaded();
  if (_descriptors && _descriptors[provider]) return _descriptors[provider];
  return FALLBACK[provider] || null;
}

/** Todos os descriptors conhecidos (fetched > fallback). Para semear catálogos. */
export function allDescriptors() {
  ensureLoaded();
  return _descriptors || FALLBACK;
}

/**
 * Lista de descriptors COMPLETOS dos providers instalados (não o fallback) —
 * `null` enquanto o fetch não chegou, para o consumidor distinguir "carregando"
 * de "nenhum provider". Fonte única do form de criação/edição da tela Canais.
 * @returns {any[]|null}
 */
export function fetchedProviders() {
  ensureLoaded();
  return _descriptors ? Object.values(_descriptors) : null;
}

/** Mapa {provider: [credential keys required]} da fetch (vazio até chegar). */
export function requiredCredentials() {
  ensureLoaded();
  return _required;
}

/**
 * Rótulo amigável do provider (o `label` do descriptor). Provider desconhecido
 * cai no próprio identificador — nunca some.
 * @param {string} provider
 */
export function providerLabel(provider) {
  const d = descriptorFor(provider);
  return (d && d.label) || provider || '—';
}

/** Token de cor do descriptor (green/blue/…); 'gray' para desconhecido. */
export function providerColor(provider) {
  const d = descriptorFor(provider);
  return (d && d.color) || 'gray';
}

/**
 * Classe de tint (bg + text) do provider, dark-mode-safe, resolvida do token de
 * cor via o mesmo `tintForColor` da tela Canais. Desconhecido → 'gray' →
 * `tintForColor('gray')` (o tint neutro), coerente com `providerColor`.
 * @param {string} provider
 */
export function providerTint(provider) {
  return tintForColor(providerColor(provider));
}

/** Tipo de contato que o provider marca (`contact_type` do descriptor). */
export function contactTypeFor(provider) {
  const d = descriptorFor(provider);
  return (d && d.contact_type) || 'outros';
}

/**
 * Mapa {contact_type: colorToken} descoberto dos descriptors, DEDUPLICADO POR
 * TIPO (dois providers podem declarar o mesmo tipo — gowa e whatsapp_cloud =
 * `whatsapp`; o primeiro vence). Alimenta o catálogo de tipos de contato
 * (contactTypes.js) para que um provider novo apareça no filtro sem editar o core.
 * @returns {Record<string, string>}
 */
export function contactTypeColorTokens() {
  const out = {};
  const all = allDescriptors();
  for (const provider of Object.keys(all)) {
    const d = all[provider];
    const ct = (d && d.contact_type) || 'outros';
    if (!(ct in out)) out[ct] = (d && d.color) || 'gray';
  }
  return out;
}

// Cor SÓLIDA (bolinha) por token de cor do descriptor — dark-mode-safe. Usada
// pelos modais de nova conversa, que mostram um dot cheio em vez de um tint.
// Desconhecido → cinza secundário.
const DOT_COLORS = {
  green: 'bg-wa-teal',
  teal: 'bg-wa-teal',
  blue: 'bg-blue-500',
  purple: 'bg-purple-500',
  amber: 'bg-amber-500',
  orange: 'bg-orange-500',
  red: 'bg-red-500',
  pink: 'bg-pink-500',
  gray: 'bg-wa-secondary',
};

/** Classe de bolinha (cor sólida) do provider. Desconhecido → cinza. */
export function providerDot(provider) {
  return DOT_COLORS[providerColor(provider)] || DOT_COLORS.gray;
}

/**
 * Meta de provider para os modais de nova conversa (rótulo + bolinha sólida) —
 * o par que ChannelPickerModal e NewConversationModal mostravam por cópias
 * estáticas idênticas. Agora um import só.
 * @param {string} provider
 * @returns {{ label: string, dot: string }}
 */
export function channelPickerMeta(provider) {
  return { label: providerLabel(provider), dot: providerDot(provider) };
}
