// Required custom attributes (P05) — gate de resolução de conversa.
//
// Atributos de conversa marcados como "Obrigatório preencher" (def.required)
// precisam ter um valor antes de a conversa ser resolvida. Este helper compara
// as definições com os valores atuais e devolve as definições ainda em falta,
// para o chamador exibir o modal e abrir o painel de preenchimento.

// Um valor "preenchido" é qualquer coisa diferente de null/undefined/""
// (string vazia ou só espaços). Checkbox `false` conta como preenchido — o
// usuário decidiu "Não"; só a ausência total do valor é considerada em falta.
function isBlank(val) {
  if (val === null || val === undefined) return true;
  if (typeof val === 'string' && val.trim() === '') return true;
  return false;
}

// Retorna a lista de definições obrigatórias cujo valor está em falta.
export function missingRequiredAttributes(defs, values) {
  const v = values || {};
  return (defs || []).filter(def => def && def.required && isBlank(v[def.attribute_key]));
}
