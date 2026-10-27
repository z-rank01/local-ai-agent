import {readdir, readFile, realpath, mkdir, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

// Only installed, source-built extensions enter the bundle. No remote JS loading.
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');
const entries = [];
const artifacts = [];
const pluginModules = new Map();
async function moduleFor(manifest, entry) {
  const bundledPrefix = `apps/web/src/plugins/${manifest.id}/`;
  const packagePrefix = `plugins/${manifest.id}/ui/`;
  const prefix = typeof entry === 'string' && entry.startsWith(packagePrefix) ? packagePrefix : bundledPrefix;
  if (typeof entry !== 'string' || !entry.startsWith(prefix) || !entry.endsWith('.tsx')) {
    throw new Error('UI entry must belong to its plugin: ' + manifest.id);
  }
  const ownedRoot = await realpath(path.join(root, prefix));
  const parentRoot = await realpath(path.join(root, prefix === packagePrefix ? `plugins/${manifest.id}` : 'apps/web/src/plugins'));
  const ownedRelative = path.relative(parentRoot, ownedRoot);
  if (ownedRelative.startsWith('..') || path.isAbsolute(ownedRelative)) throw new Error('UI package escapes plugin root');
  const actual = await realpath(path.join(root, entry));
  const relative = path.relative(ownedRoot, actual);
  if (relative.startsWith('..') || path.isAbsolute(relative)) throw new Error('UI entry escapes plugin: ' + manifest.id);
  const module = path.relative(path.join(root, 'apps/web/src/extensions'), actual).replaceAll('\\', '/').replace(/\.tsx$/, '');
  return module.startsWith('.') ? module : './' + module;
}
for (const directory of (await readdir(path.join(root, 'plugins'), {withFileTypes: true})).sort((a,b) => a.name.localeCompare(b.name))) {
  if (!directory.isDirectory()) continue;
  const manifestPath = path.join(root, 'plugins', directory.name, 'plugin.json');
  let manifest;
  try {manifest = JSON.parse(await readFile(manifestPath, 'utf8'));}
  catch (error) {if (error.code === 'ENOENT') continue; throw error;}
  const entry = manifest.ui?.settings;
  const artifact = manifest.artifacts;
  if (!entry && !artifact) continue;
  if (manifest.id !== directory.name || !/^[a-z][a-z0-9-]*$/.test(manifest.id) || manifest.harness_api !== 1) {
    throw new Error('Invalid UI plugin identity: ' + directory.name);
  }
  if (entry) {
    const module = await moduleFor(manifest, entry);
    const metadata = {id: manifest.id, title: manifest.ui.title ?? manifest.title, description: manifest.ui.description ?? manifest.description};
    if (![metadata.title, metadata.description].every(value => typeof value === 'string')) throw new Error('UI metadata missing');
    entries.push({order: Number.isFinite(manifest.ui.order) ? manifest.ui.order : 100, source: `  {...${JSON.stringify(metadata)}, load: () => import(${JSON.stringify(module)})}`});
    pluginModules.set(manifest.id, module);
  }
  if (artifact) {
    if (typeof artifact.type !== 'string' || !/^[a-z][a-z0-9-]*$/.test(artifact.type)
      || !Array.isArray(artifact.tools) || !artifact.tools.length
      || !artifact.tools.every(tool => typeof tool === 'string' && tool)
      || new Set(artifact.tools).size !== artifact.tools.length) throw new Error('Artifact metadata missing: ' + manifest.id);
    const module = await moduleFor(manifest, artifact.renderer);
    artifacts.push(`  {type: ${JSON.stringify(artifact.type)}, tools: ${JSON.stringify(artifact.tools)}, load: () => import(${JSON.stringify(module)})}`);
  }
}
const destination = path.join(root, 'apps/web/src/extensions/generated.ts');
await mkdir(path.dirname(destination), {recursive: true});
await writeFile(destination, "// Generated from plugin.json. Do not edit.\nimport type {ArtifactContribution, UIContribution} from './contracts';\nexport const contributions: UIContribution[] = [\n" + entries.sort((a,b) => a.order - b.order).map(entry => entry.source).join(',\n') + '\n];\nexport const artifactContributions: ArtifactContribution[] = [\n' + artifacts.join(',\n') + '\n];\n');
console.log(`Registered ${entries.length} UI and ${artifacts.length} artifact contributions.`);
