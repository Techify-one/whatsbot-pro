/**
 * Smoke de execução de uma screen de plugin.
 *
 * `node --check` valida SINTAXE e não pega os dois erros que já escaparam para a
 * tela do Trackify: uma chamada a um setter de estado que foi removido, e um
 * `const` usado no efeito de montagem ANTES de ser declarado (temporal dead
 * zone). Os dois são ReferenceError em runtime e deixam o modal em branco.
 *
 * Aqui o módulo é de fato importado e o componente é CHAMADO, com preact/htm
 * substituídos por dublês. Não renderiza a árvore — o objetivo é executar o
 * corpo das funções, que é onde esses erros moram.
 *
 * Uso: node smoke_plugin_screen.mjs <dir-do-plugin> <arquivo.js> [componente...]
 */
import { mkdtemp, writeFile, readFile, cp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, basename } from 'node:path';

const [dir, entry, ...componentes] = process.argv.slice(2);

const STUB_HOOKS = `
export const useState = (init) => [typeof init === 'function' ? init() : init, () => {}];
export const useEffect = () => {};      // não roda: o efeito faz fetch
export const useCallback = (fn) => fn;
export const useMemo = (fn) => fn();
export const useRef = (v) => ({ current: v });
`;
const STUB_PREACT = `export const h = (...a) => ({ __vnode: a });`;
// htm devolve uma tag; o dublê só precisa aceitar qualquer interpolação.
const STUB_HTM = `export default () => (s, ...v) => ({ __html: s, values: v });`;
const STUB_API = `export const authHeaders = () => ({});`;

const tmp = await mkdtemp(join(tmpdir(), 'screen-smoke-'));
await cp(dir, tmp, { recursive: true });
await writeFile(join(tmp, '__hooks.js'), STUB_HOOKS);
await writeFile(join(tmp, '__preact.js'), STUB_PREACT);
await writeFile(join(tmp, '__htm.js'), STUB_HTM);
await writeFile(join(tmp, '__api.js'), STUB_API);

// Reescreve os especificadores de import de TODOS os .js copiados.
const { readdir } = await import('node:fs/promises');
for (const f of (await readdir(tmp)).filter((n) => n.endsWith('.js'))) {
  const p = join(tmp, f);
  let src = await readFile(p, 'utf8');
  src = src
    .replace(/from ['"]preact\/hooks['"]/g, "from './__hooks.js'")
    .replace(/from ['"]preact['"]/g, "from './__preact.js'")
    .replace(/from ['"]htm['"]/g, "from './__htm.js'")
    .replace(/from ['"]\/static\/js\/services\/api\.js['"]/g, "from './__api.js'")
    .replace(/from ['"]\/plugins\/[^'"]*\/static\/([^'"]+)['"]/g, "from './$1'");
  await writeFile(p, src);
}

const mod = await import(join(tmp, basename(entry)));

// Props mínimas: o corpo do componente não pode depender delas para EXECUTAR.
const props = { apiBase: '/api/plugins/x', req: async () => ({ ok: true, data: {} }),
                settings: {}, values: {}, data: {}, state: {}, rows: [],
                onSave: () => {}, onSaveSettings: () => {}, onRefresh: () => {},
                onTest: () => {}, onChange: () => {}, busy: false, can: () => true };

const alvos = componentes.length ? componentes : ['default'];
for (const nome of alvos) {
  const C = mod[nome];
  if (typeof C !== 'function') {
    console.error(`FALHOU: ${nome} não é um componente exportado por ${entry}`);
    process.exit(1);
  }
  C(props);   // executa o corpo — é aqui que TDZ e setter fantasma estouram
}
console.log('ok');
