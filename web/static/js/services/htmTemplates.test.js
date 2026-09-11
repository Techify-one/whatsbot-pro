// Run with: node --test web/static/js/services/htmTemplates.test.js
//
// Rede de segurança para a armadilha mais cara do htm: uma CRASE dentro de um
// template html`...` FECHA o template ali mesmo. O resto do JSX vira código
// JavaScript solto, o componente lança em runtime e — como Preact não renderiza
// a subárvore de um componente que lança — a peça inteira **some da tela sem
// erro visível**.
//
// Isso já custou uma release: a barra de busca do chat (plano 99) foi entregue
// com o comentário `<!-- \`.wa-field\` vai NO INPUT -->` dentro do template, e o
// header do chat simplesmente desaparecia ao clicar na lupa.
//
// ⚠️ `node --check` NÃO pega: um par de crases deixa o arquivo sintaticamente
// válido (`html\`…\`.wa-field\`…\`` é acesso a propriedade seguido de outro
// template). Só uma verificação semântica como esta acusa.
//
// A heurística: dentro de um bloco html`…`, um `<!--` sem o `-->` correspondente
// significa que o bloco foi cortado no meio de um comentário — ou seja, uma
// crase o fechou antes da hora.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const JS_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

function allJsFiles(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) allJsFiles(p, out);
    else if (name.endsWith('.js') && !name.endsWith('.test.js')) out.push(p);
  }
  return out;
}

/** Blocos html`…` de um arquivo, com a linha em que cada um começa. */
function htmBlocks(src) {
  const blocks = [];
  const re = /html`/g;
  let m;
  while ((m = re.exec(src)) !== null) {
    const start = m.index + m[0].length;
    const end = src.indexOf('`', start);
    blocks.push({
      line: src.slice(0, m.index).split('\n').length,
      body: src.slice(start, end === -1 ? src.length : end),
    });
  }
  return blocks;
}

test('nenhuma crase dentro de comentário em template htm', () => {
  const culpados = [];
  for (const file of allJsFiles(JS_ROOT)) {
    const src = readFileSync(file, 'utf8');
    for (const { line, body } of htmBlocks(src)) {
      const abertos = (body.match(/<!--/g) || []).length;
      const fechados = (body.match(/-->/g) || []).length;
      if (abertos > fechados) {
        culpados.push(`${file.slice(JS_ROOT.length + 1)}:${line}`);
      }
    }
  }
  assert.deepEqual(culpados, [],
    'template html`…` cortado no meio de um comentário HTML — quase sempre uma '
    + 'CRASE dentro do comentário, que fecha o template e faz o componente sumir '
    + 'da tela sem erro visível. Reescreva o comentário sem crase.');
});

// ⚠️ SEGUNDA rede (2026-08-18): a heurística acima tem um furo estrutural — ela
// fatia de cada `html\`` até a PRÓXIMA crase, então um comentário defeituoso que
// caia ENTRE dois templates aninhados nunca chega a ser inspecionado. Foi assim
// que um comentário com um número PAR de crases (``foo``) entrou no
// AiSettingsFields.js, passou por esta suíte e por `node --input-type=module
// --check`, e derrubou a tela de Canais: o modal de configuração nunca abria,
// com `html(...) is not a function` no console.
//
// Esta checagem é textual e não depende de achar as fronteiras do template:
// comentário HTML NUNCA leva crase, ponto. Se precisar citar código, tire o
// comentário de dentro do template.
test('nenhum comentário HTML contém crase (vale em qualquer lugar do arquivo)', () => {
  const culpados = [];
  for (const file of allJsFiles(JS_ROOT)) {
    const src = readFileSync(file, 'utf8');
    for (const m of src.matchAll(/<!--[\s\S]*?-->/g)) {
      if (m[0].includes('`')) {
        culpados.push(`${file.slice(JS_ROOT.length + 1)}:${src.slice(0, m.index).split('\n').length}`);
      }
    }
  }
  assert.deepEqual(culpados, [],
    'comentário HTML com CRASE. Dentro de um template htm ela fecha o template e '
    + 'o componente lança em runtime (a peça some da tela / o modal não abre). '
    + 'Reescreva o comentário sem crase.');
});
