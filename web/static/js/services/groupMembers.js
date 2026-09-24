// Lista de membros de um grupo (GroupMembersModal.js) — a decisão de "o que mostrar e
// para onde o clique leva" mora aqui, pura (sem preact/DOM/rede), para ser testável
// com ``node --test``.
//
// A entrada é o roster do backend (``GET /api/contacts/<grupo>/members``):
//   { phone, lid, name, is_admin }
// ``phone`` é o telefone REAL do participante e vem vazio quando o grupo o endereça só
// por LID — LID é um id opaco, NÃO telefone, então esse membro aparece na lista mas
// não vira link (criar contato com ele validaria um número que não existe). Mesma
// regra do backend (agent/group_mentions.py ``_member_phone``): 10–15 dígitos.

import { formatPhoneDisplay } from '../utils/phone.js';
import { memberHref } from './systemMemberLinks.js';

const PHONE_RE = /^\d{10,15}$/;

// Casefold + tira acento — espelha o ``fold`` da busca do autocomplete.
function fold(s) {
  return (s || '').normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

// Prioridade de ordenação: quem tem nome, depois quem só tem telefone, por último o
// participante sem nome nem telefone.
function tier(entry) {
  if (entry.hasName) return 0;
  return entry.phone ? 1 : 2;
}

// Membros prontos para renderizar, já filtrados por ``query`` e ordenados.
//   label — nome; ou o telefone formatado quando não há nome
//   sub   — linha secundária (telefone quando há nome; aviso quando não há telefone)
//   href  — destino do clique ("Novo contato" pré-preenchido) ou ``null`` sem telefone
export function memberEntries(members, query = '') {
  const list = Array.isArray(members) ? members : [];
  const q = fold(query).trim();
  const qDigits = q.replace(/\D/g, '');

  const entries = list.filter(Boolean).map((m, i) => {
    const phone = PHONE_RE.test(m.phone || '') ? m.phone : '';
    // "~Fulano" é a marca de pushName (nome que a pessoa se deu, não salvo) — o painel
    // de contato também a tira, e não deve ir para o campo Nome de um contato novo.
    const name = (m.name || '').replace(/^~/, '').trim();
    const phoneLabel = phone ? formatPhoneDisplay(phone) : '';
    return {
      key: phone || m.lid || `idx-${i}`,
      hasName: !!name,
      name,
      phone,
      label: name || phoneLabel || 'Participante sem número',
      sub: name
        ? (phoneLabel || 'Número indisponível')
        : (phone ? '' : 'Número indisponível'),
      isAdmin: !!m.is_admin,
      href: phone ? memberHref(phone, name) : null,
    };
  });

  const shown = q
    ? entries.filter((e) => fold(e.name).includes(q) || (qDigits && e.phone.includes(qDigits)))
    : entries;

  return shown.sort((a, b) => (
    tier(a) - tier(b) || a.label.localeCompare(b.label, 'pt-BR', { sensitivity: 'base' })
  ));
}

export function membersCountLabel(n) {
  return n === 1 ? '1 participante' : `${n} participantes`;
}
