// @ts-check
//
// Catálogo de TIPOS DE CONTATO (plano tipos-de-contato). O tipo é herdado do canal
// que materializou o contato (o provider declara via `Channel.contact_type()` no
// backend) e persiste em `contacts.contact_type`. Aqui mora só o mapa de exibição
// (rótulo + cor) usado pela marca no painel do contato e pelo filtro por tipo.
//
// PURO: sem preact, sem DOM, sem rede. Cores em hex para casarem com o formato de
// chip das tags (background {color}20, texto {color}, borda {color}40).

// `neutral: true` ⇒ badge renderizado com classes semânticas wa-* (legível nos dois
// temas), em vez de hex inline. Marcas de brand (whatsapp/telegram) usam hex.
/** @type {Record<string, { label: string, color: string, neutral?: boolean }>} */
export const CONTACT_TYPE_META = {
  whatsapp: { label: 'WhatsApp', color: '#25d366' },
  telegram: { label: 'Telegram', color: '#2aabee' },
  outros:   { label: 'Outros',   color: '#6b7280', neutral: true },
};

/** Ordem canônica dos tipos conhecidos (para dropdowns/filtros). */
export const CONTACT_TYPE_ORDER = ['whatsapp', 'telegram', 'outros'];

/**
 * Metadados de exibição de um tipo. Tipos desconhecidos (um provider novo que
 * ainda não está no mapa) caem num rótulo capitalizado + estilo neutro, então a UI
 * nunca quebra ao encontrar um tipo inesperado.
 * @param {string|null|undefined} type
 * @returns {{ label: string, color: string, neutral?: boolean }}
 */
export function contactTypeMeta(type) {
  const key = (type || 'outros').toLowerCase();
  if (CONTACT_TYPE_META[key]) return CONTACT_TYPE_META[key];
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
