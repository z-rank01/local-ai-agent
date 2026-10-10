import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const base = new URL('../src/lib/', import.meta.url);
const bootstrap = readFileSync(new URL('../index.html', import.meta.url), 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];

function fixture({raw = null, dark = false, blocked = false, legacy = false, unavailable = false, throwsMedia = false} = {}) {
  const listeners = new Set();
  const media = {matches: dark};
  if (legacy) {
    media.addListener = listener => listeners.add(listener);
    media.removeListener = listener => listeners.delete(listener);
  } else {
    media.addEventListener = (_, listener) => listeners.add(listener);
    media.removeEventListener = (_, listener) => listeners.delete(listener);
  }
  const document = {documentElement: {dataset: {}}};
  const localStorage = {
    getItem(key) { assert.equal(key, 'local-ai-agent.appearance.v1'); if (blocked) throw new Error('blocked'); return raw; },
    setItem() { if (blocked) throw new Error('blocked'); },
  };
  const matchMedia = unavailable ? undefined : () => { if (throwsMedia) throw new Error('unavailable'); return media; };
  const window = {localStorage, matchMedia};
  const cache = new Map();
  function load(name) {
    if (cache.has(name)) return cache.get(name);
    const module = {exports: {}};
    cache.set(name, module.exports);
    const source = readFileSync(new URL(name + '.ts', base), 'utf8');
    const code = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
    vm.runInNewContext(code, {module, exports: module.exports, require: spec => spec.startsWith('./') ? load(spec.slice(2)) : require(spec), window, document});
    return module.exports;
  }
  return {document, media, listeners, load,
    bootstrap() { vm.runInNewContext(bootstrap, {document, localStorage, matchMedia}); return document.documentElement.dataset.theme; },
    change(value) { media.matches = value; for (const listener of listeners) listener(); },
  };
}

test('old appearance preserves typography and defaults to system', () => {
  const old = {uiFont: 'Example', contentFontSize: 18, codeFontSize: 14, readingWidth: 900, lineHeight: 1.8, density: 'compact'};
  const f = fixture({raw: JSON.stringify(old)});
  const result = f.load('appearance').loadAppearanceSettings();
  for (const [key, value] of Object.entries(old)) assert.equal(result[key], value);
  assert.equal(result.theme, 'system');
  assert.equal(f.load('appearance').DEFAULT_APPEARANCE.theme, 'system');
});

for (const value of [undefined, 'system', 'light', 'dark', 'invalid', 1, null]) {
  for (const dark of [false, true]) {
    test(`bootstrap and runtime agree: ${String(value)}, system dark=${dark}`, () => {
      const f = fixture({raw: JSON.stringify({theme: value}), dark});
      const first = f.bootstrap();
      const mode = f.load('appearance').loadAppearanceSettings().theme;
      const cleanup = f.load('theme').watchTheme(mode);
      assert.equal(f.document.documentElement.dataset.theme, first);
      cleanup();
      assert.equal(f.listeners.size, 0);
    });
  }
}

for (const options of [{raw: '{broken', dark: true}, {raw: 'null', dark: true}, {blocked: true, dark: true}, {unavailable: true}, {throwsMedia: true}]) {
  test(`storage/media fallback: ${JSON.stringify(options)}`, () => {
    const f = fixture(options);
    const first = f.bootstrap();
    const cleanup = f.load('theme').watchTheme(f.load('appearance').loadAppearanceSettings().theme);
    assert.equal(f.document.documentElement.dataset.theme, first);
    assert.equal(first, options.dark ? 'dark' : 'light');
    cleanup();
  });
}

for (const legacy of [false, true]) {
  test(`system follows changes and cleans up ${legacy ? 'legacy' : 'modern'} listener`, () => {
    const f = fixture({legacy});
    const cleanup = f.load('theme').watchTheme('system');
    assert.equal(f.listeners.size, 1);
    f.change(true); assert.equal(f.document.documentElement.dataset.theme, 'dark');
    f.change(false); assert.equal(f.document.documentElement.dataset.theme, 'light');
    cleanup(); assert.equal(f.listeners.size, 0);
    f.change(true); assert.equal(f.document.documentElement.dataset.theme, 'light');
    const secondCleanup = f.load('theme').watchTheme('system');
    assert.equal(f.listeners.size, 1); secondCleanup();
  });
}

for (const mode of ['light', 'dark']) {
  test(`explicit ${mode} ignores system changes`, () => {
    const f = fixture();
    const cleanup = f.load('theme').watchTheme(mode);
    assert.equal(f.listeners.size, 0);
    f.change(true); assert.equal(f.document.documentElement.dataset.theme, mode);
    f.change(false); assert.equal(f.document.documentElement.dataset.theme, mode);
    cleanup();
  });
}
