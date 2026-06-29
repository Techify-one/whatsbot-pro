#!/usr/bin/env node
// Import-graph integrity checker for the build-less ESM frontend (Plano 23 Wave 4).
//
// The frontend has no bundler, so a decomposition that moves/renames an export or
// mistypes a relative path fails only at runtime in the browser. This script walks
// every web/static/js/**/*.js, resolves each *relative* import/dynamic-import target,
// and fails if any path does not exist on disk. Bare specifiers (preact, htm, …) are
// provided by the import map and skipped. Run: `node tests/frontend/check_imports.mjs`.
import { readdirSync, readFileSync, statSync, existsSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', 'web', 'static', 'js');

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) walk(p, out);
    else if (name.endsWith('.js') || name.endsWith('.mjs')) out.push(p);
  }
  return out;
}

// Matches:  import ... from 'x'  |  export ... from 'x'  |  import('x')  |  import 'x'
const RE = /(?:import|export)\s+(?:[^'"]*?\sfrom\s+)?['"]([^'"]+)['"]|import\(\s*['"]([^'"]+)['"]\s*\)/g;

let problems = 0;
let checked = 0;
for (const file of walk(ROOT)) {
  // Strip block + line comments so example import paths inside docs don't register
  // as real imports (they routinely show `from '.../api.js'` as illustration).
  const src = readFileSync(file, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:'"])\/\/[^\n]*/g, '$1');
  let m;
  while ((m = RE.exec(src)) !== null) {
    const spec = m[1] || m[2];
    if (!spec) continue;
    // Only relative specifiers point at files on disk; bare ones come from the import map.
    if (!spec.startsWith('.') && !spec.startsWith('/')) continue;
    checked++;
    let target;
    if (spec.startsWith('/')) {
      // Absolute browser path: '/static/js/...'  -> repo web/<path after /static>... actually served from web/
      target = resolve(ROOT, '..', '..', '..', 'web', spec.replace(/^\//, ''));
      // The app mounts web/ at '/static' (and '/plugins'); map '/static/js/x' -> web/static/js/x
      target = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', 'web', spec.replace(/^\//, ''));
    } else {
      target = resolve(dirname(file), spec);
    }
    const candidates = [target, target + '.js', join(target, 'index.js')];
    if (!candidates.some((c) => existsSync(c))) {
      console.error(`BROKEN IMPORT  ${file.replace(ROOT, 'js')}  ->  ${spec}`);
      problems++;
    }
  }
}

console.log(`checked ${checked} relative imports across ${walk(ROOT).length} files`);
if (problems) { console.error(`\n${problems} broken import(s)`); process.exit(1); }
console.log('all relative imports resolve ✓');
