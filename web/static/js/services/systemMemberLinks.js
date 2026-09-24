// Nome de participante clicável nos avisos de grupo ("João entrou no grupo").
//
// O backend (agent/group_mentions.py `_member_mark`) grava cada participante com
// telefone conhecido como o token ``[[member:<telefone>|<nome>]]`` dentro do
// `content` do `system_notice`. O SystemMessageCard chama ``parseMemberLinks`` para
// trocar o token por um link para "Novo contato" — o MESMO destino que o rótulo do
// remetente numa bolha de grupo usa (MessageBubble.js). Participante sem telefone
// (só-LID não resolvido) chega como texto puro e simplesmente não vira link; linhas
// antigas, gravadas antes do token existir, também.
//
// Puro (sem dependências) para ser testável com ``node --test``.

const MEMBER_RE = /\[\[member:(\d{10,15})\|([^\]]*)\]\]/g;

// Devolve segmentos na ordem do texto: ``{ type: 'text', text }`` e
// ``{ type: 'member', phone, label, name, href }``.
//   label — o que aparece no card (nome, ou "+<telefone>" quando o participante
//           não tem nome).
//   name  — o que pré-preenche o campo Nome do modal; vazio quando o rótulo é só
//           o telefone (não faz sentido gravar "+5511…" como nome do contato).
// Conteúdo sem token volta como UM segmento de texto (render idêntico ao de antes).
export function parseMemberLinks(content) {
  const text = typeof content === 'string' ? content : '';
  const segments = [];
  let last = 0;
  for (const m of text.matchAll(MEMBER_RE)) {
    if (m.index > last) segments.push({ type: 'text', text: text.slice(last, m.index) });
    const phone = m[1];
    const label = (m[2] || '').trim() || `+${phone}`;
    const name = label.replace(/\D/g, '') === phone ? '' : label;
    segments.push({ type: 'member', phone, label, name, href: memberHref(phone, name) });
    last = m.index + m[0].length;
  }
  if (last < text.length || segments.length === 0) {
    segments.push({ type: 'text', text: text.slice(last) });
  }
  return segments;
}

// Destino do clique: a tela de contatos abre o modal "Novo contato" pré-preenchido
// (par one-shot `createPhone`/`createName`, ver ContactsListScreen.js), que já
// bloqueia a criação e oferece "Ver detalhes" quando o número é um contato salvo.
export function memberHref(phone, name) {
  const base = `/contacts?createPhone=${encodeURIComponent(phone)}`;
  return name ? `${base}&createName=${encodeURIComponent(name)}` : base;
}
