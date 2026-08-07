/**
 * Procura setters de estado CHAMADOS mas nunca declarados.
 *
 * Complemento do smoke de execução: um `setX(...)` dentro de um `useEffect` não
 * é alcançado por ele (o dublê não roda o efeito), mas explode na tela do
 * usuário. Foi assim que `setNewDsn(null)` sobreviveu à remoção do estado
 * `newDsn` e deixou a aba Conexão do Trackify em branco.
 *
 * Heurística deliberadamente estreita: só olha identificadores no formato
 * `setAlgumaCoisa` (a convenção de `useState`). Um setter recebido por prop é
 * declarado como parâmetro e por isso não acusa.
 *
 * Uso: node lint_phantom_setters.mjs <arquivo.js> [...]
 */
import { readFile } from 'node:fs/promises';

const SETTER = /\bset[A-Z]\w*/g;

let falhou = false;

for (const arquivo of process.argv.slice(2)) {
  const src = await readFile(arquivo, 'utf8');

  // Declarados por useState: `const [x, setX] = useState(...)`
  const declarados = new Set(
    [...src.matchAll(/\[\s*\w+\s*,\s*(set[A-Z]\w*)\s*\]\s*=/g)].map((m) => m[1]),
  );
  // Declarados de outras formas: parâmetro/prop/const/função local.
  for (const m of src.matchAll(/(?:const|let|var|function)\s+(set[A-Z]\w*)/g)) {
    declarados.add(m[1]);
  }
  for (const m of src.matchAll(/\{([^}]*)\}\s*\)\s*\{/g)) {
    for (const n of m[1].match(SETTER) || []) declarados.add(n);
  }
  for (const m of src.matchAll(/\(([^)]*)\)\s*=>/g)) {
    for (const n of m[1].match(SETTER) || []) declarados.add(n);
  }

  const chamados = new Set(
    [...src.matchAll(/\b(set[A-Z]\w*)\s*\(/g)].map((m) => m[1]),
  );

  const fantasmas = [...chamados].filter((n) => !declarados.has(n));
  if (fantasmas.length) {
    falhou = true;
    console.error(
      `${arquivo}: setter(es) chamado(s) mas nunca declarado(s): ${fantasmas.join(', ')}`,
    );
  }
}

if (falhou) process.exit(1);
console.log('ok');
