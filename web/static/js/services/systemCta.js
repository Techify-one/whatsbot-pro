// Call-to-action opcional em cards de sistema ("Sistema" / role=system).
//
// Um produtor de mensagem (core OU plugin) pode anexar um botão de ação ao card
// codificando-o no conteúdo como o token ``[[cta:RÓTULO|URL]]`` (ex.: o plugin
// melhorias posta "…enviada ao painel.\n[[cta:Ir para sugestão|https://…]]").
// O SystemMessageCard chama ``parseCta`` para separar o texto do botão.
//
// Puro (sem dependências) para ser testável com ``node --test``.

const CTA_RE = /\[\[cta:([^|\]]+)\|([^\]]+)\]\]/;

// Aceita só http(s) absoluto ou caminho interno (``/...``) como destino — guarda
// contra ``javascript:``/``data:`` e afins, já que o href é renderizado.
function isSafeUrl(url) {
  return /^(https?:\/\/|\/)/i.test(url);
}

// Devolve ``{ text, action }`` — ``text`` é o conteúdo sem o token (pronto para
// formatar/renderizar) e ``action`` = ``{ label, url }`` ou ``null`` (sem token,
// rótulo vazio ou destino inseguro). Idempotente e tolerante a conteúdo vazio.
export function parseCta(content) {
  const text = content || '';
  const m = text.match(CTA_RE);
  if (!m) return { text, action: null };
  const label = (m[1] || '').trim();
  const url = (m[2] || '').trim();
  const cleaned = text.replace(m[0], '').trim();
  const action = (label && isSafeUrl(url)) ? { label, url } : null;
  return { text: cleaned, action };
}
