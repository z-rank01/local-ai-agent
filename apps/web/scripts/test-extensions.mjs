import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, mkdir, copyFile, writeFile, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';

test('manifest addition and removal regenerate UI without editing host imports; escapes fail safely', async () => {
  const root = await mkdtemp(path.join(tmpdir(), 'harness-ui-'));
  try {
    const scripts = path.join(root, 'apps/web/scripts');
    await mkdir(scripts, {recursive: true});
    const generator = path.join(scripts, 'generate-extensions.mjs');
    await copyFile(path.join(path.dirname(fileURLToPath(import.meta.url)), 'generate-extensions.mjs'), generator);
    const add = async id => {
      await mkdir(path.join(root, 'plugins', id), {recursive: true});
      const entry = `apps/web/src/plugins/${id}/index.tsx`;
      await mkdir(path.dirname(path.join(root, entry)), {recursive: true});
      await writeFile(path.join(root, entry), 'export default function Panel() { return null; }');
      await writeFile(path.join(root, 'plugins', id, 'plugin.json'), JSON.stringify({
        id, title: id, description: 'test', harness_api: 1, ui: {settings: entry},
      }));
    };
    const build = () => execFileSync(process.execPath, [generator], {stdio: 'pipe'});
    const output = path.join(root, 'apps/web/src/extensions/generated.ts');
    await add('first'); build();
    assert.match(await readFile(output, 'utf8'), /plugins\/first\/index/);
    await add('second'); build();
    assert.match(await readFile(output, 'utf8'), /plugins\/second\/index/);
    // Removing only the manifest uninstalls its contribution from the registry.
    await rm(path.join(root, 'plugins/first/plugin.json')); build();
    const previous = await readFile(output, 'utf8');
    assert.doesNotMatch(previous, /plugins\/first\/index/);
    await writeFile(path.join(root, 'plugins/second/plugin.json'), JSON.stringify({
      id: 'second', harness_api: 1, ui: {settings: 'apps/web/src/plugins/second/../first/index.tsx'},
    }));
    assert.throws(build);
    assert.equal(await readFile(output, 'utf8'), previous);
  } finally {
    assert.equal(path.dirname(path.resolve(root)), path.resolve(tmpdir()));
    assert.ok(path.basename(root).startsWith('harness-ui-'));
    await rm(root, {recursive: true, force: true});
  }
});
