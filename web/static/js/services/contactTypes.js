// @ts-check
//
// Catálogo de TIPOS DE CONTATO (plano tipos-de-contato). O tipo é herdado do canal
// que materializou o contato (o provider declara via `Channel.contact_type()` no
// backend) e persiste em `contacts.contact_type`. Aqui mora só o mapa de exibição
// (rótulo + cor) usado pela marca no painel do contato e pelo filtro por tipo.
//
// Cores em hex para casarem com o formato de chip das tags (background {color}20,
// texto {color}, borda {color}40).
//
// Plano 76 · V5 — o mapa estático abaixo é a BASE CURADA (rótulos + cores de brand
// dos tipos conhecidos, e o fallback `outros`). Tipos NOVOS (um provider recém
// instalado, ex.: o `site` do widget) são DESCOBERTOS do catálogo de providers
// (providerCatalog.contactTypeColorTokens) e semeados por cima — deduplicados por
// tipo. Assim um provider novo aparece no filtro sem editar o core, sem perder as
// cores de brand curadas. O catálogo cai num fallback estático sem rede, então
// isto continua funcionando (só sem os tipos ainda-não-carregados) antes do fetch.
import { contactTypeColorTokens } from './providerCatalog.js';

// Token de cor do descriptor → hex para o chip de tipo (o descriptor fala em
// tokens verde/azul/…, o badge de tipo pinta com hex translúcido).
const TOKEN_HEX = {
  green: '#25d366',
  teal: '#0ea5a4',
  blue: '#0866ff',
  purple: '#8b5cf6',
  amber: '#f59e0b',
  orange: '#f97316',
  red: '#ef4444',
  pink: '#ec4899',
  gray: '#6b7280',
};

// `neutral: true` ⇒ badge renderizado com classes semânticas wa-* (legível nos dois
// temas), em vez de hex inline. Marcas de brand (whatsapp/telegram) usam hex.
/** @type {Record<string, { label: string, color: string, neutral?: boolean }>} */
export const CONTACT_TYPE_META = {
  whatsapp: { label: 'WhatsApp', color: '#25d366' },
  telegram: { label: 'Telegram', color: '#2aabee' },
  facebook: { label: 'Facebook', color: '#0866ff' },
  outros:   { label: 'Outros',   color: '#6b7280', neutral: true },
};

/** Ordem canônica dos tipos conhecidos (base curada, para dropdowns/filtros). */
export const CONTACT_TYPE_ORDER = ['whatsapp', 'telegram', 'facebook', 'outros'];

/**
 * Ordem completa dos tipos: a base curada + os tipos DESCOBERTOS do catálogo que
 * ainda não estão nela (ex.: `site`), com `outros` sempre por último. Deduplicado.
 * @returns {string[]}
 */
export function contactTypeOrder() {
  const seen = new Set(CONTACT_TYPE_ORDER);
  const discovered = Object.keys(contactTypeColorTokens())
    .filter((t) => t && t !== 'outros' && !seen.has(t));
  const base = CONTACT_TYPE_ORDER.filter((t) => t !== 'outros');
  return [...base, ...discovered, 'outros'];
}

/**
 * Metadados de exibição de um tipo. Ordem de resolução: base curada → tipo
 * descoberto do catálogo (cor do descriptor, rótulo capitalizado) → fallback
 * capitalizado neutro. A UI nunca quebra ao encontrar um tipo inesperado.
 * @param {string|null|undefined} type
 * @returns {{ label: string, color: string, neutral?: boolean }}
 */
export function contactTypeMeta(type) {
  const key = (type || 'outros').toLowerCase();
  if (CONTACT_TYPE_META[key]) return CONTACT_TYPE_META[key];
  const token = contactTypeColorTokens()[key];
  if (token) {
    const label = key.charAt(0).toUpperCase() + key.slice(1);
    return { label, color: TOKEN_HEX[token] || '#6b7280' };
  }
  const label = key ? key.charAt(0).toUpperCase() + key.slice(1) : 'Outros';
  return { label, color: '#6b7280', neutral: true };
}

/**
 * Atributos de renderização de um badge de tipo, com legibilidade garantida nos
 * dois temas: tipos neutros (outros/desconhecido) usam classes semânticas wa-*;
 * tipos de brand usam o chip tingido (hex translúcido). Cada call site compõe com
 * suas próprias classes de tamanho.
 * @param {string|null|undefined} type
 * @returns {{ label: string, className: string, style: string }}
 */
export function contactTypeBadge(type) {
  const meta = contactTypeMeta(type);
  if (meta.neutral) {
    return { label: meta.label,
      className: 'bg-wa-hover text-wa-secondary border border-wa-border', style: '' };
  }
  return { label: meta.label, className: '',
    style: `background: ${meta.color}20; color: ${meta.color}; border: 1px solid ${meta.color}40;` };
}
