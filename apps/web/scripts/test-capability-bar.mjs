import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const base = new URL('../src/panels/capabilities/', import.meta.url);

function load(cache, name) {
  if (cache.has(name)) return cache.get(name);
  const module = {exports: {}};
  cache.set(name, module.exports);
  const source = readFileSync(new URL(`${name}.ts`, base), 'utf8');
  const code = ts.transpileModule(source, {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
  }).outputText;
  vm.runInNewContext(code, {
    module,
    exports: module.exports,
    require: (spec) => (spec.startsWith('./') ? load(cache, spec.slice(2)) : require(spec)),
  });
  return module.exports;
}

const model = load(new Map(), 'capabilityModel');
// Arrays returned by the vm-realm module have a different prototype; copy them
// into this realm before strict comparisons.
const array = (value) => [...value];

function fixtures() {
  const plugins = [
    {
      id: 'websearch', title: '联网搜索', description: '检索公开网页并读取正文',
      registration: 'registered', tools: ['web_search'],
      lifecycle: {search: {state: 'ACTIVE', accepting: true, error: '', inflight: 0}},
    },
    {
      id: 'finance', title: '金融研究', description: '研究任务与报告',
      registration: 'registered', tools: ['stock_report_read'],
      lifecycle: {stock: {state: 'START_FAILED', accepting: false, error: 'boom', inflight: 0}},
    },
    {
      id: 'document-index', title: '资料索引', description: '定位原文件段落',
      registration: 'unregistered', tools: [],
    },
  ];
  const skills = [
    {id: 'meeting-notes', enabled: true, active: {description: '把会议记录整理为决议和待办'},
      pending: null, history: [], blockers: []},
    {id: 'table-reconcile', enabled: false, active: {description: '核对两份表格'},
      pending: null, history: [], blockers: []},
    {id: 'document-compare', enabled: true, active: {description: '比较多份资料'},
      pending: {description: '待激活版本'}, history: [], blockers: []},
    {id: 'harness-stock-research-v4', enabled: true, active: {description: '聊天投研方法'},
      pending: null, history: [], blockers: []},
  ];
  return {plugins, packages: [], skills};
}

test('display names and icons fall back without emoji or remote assets', () => {
  assert.equal(model.skillDisplayName('meeting-notes'), '会议整理');
  assert.equal(model.skillDisplayName('harness-public-sources-v2'), '公开来源 v2');
  assert.equal(model.skillDisplayName('harness-stock-research-v4'), '聊天投研 v4');
  assert.equal(model.skillDisplayName('custom-skill'), 'custom-skill');
  assert.equal(model.capabilityIcon('meeting-notes', 'skill'), 'i-doc');
  assert.equal(model.capabilityIcon('harness-public-sources-v1', 'skill'), 'i-search');
  assert.equal(model.capabilityIcon('custom-skill', 'skill'), 'i-spark');
  assert.equal(model.capabilityIcon('websearch', 'plugin'), 'i-wifi');
  assert.equal(model.capabilityIcon('custom-plugin', 'plugin'), 'i-grid');
});

test('library maps real inventory statuses and keeps unavailable rows', () => {
  const {plugins, packages, skills} = fixtures();
  const library = model.buildCapabilityLibrary(plugins, packages, skills);
  const byId = Object.fromEntries(library.map((item) => [item.id, item]));
  assert.equal(library.length, 7);
  assert.equal(byId['plugin:websearch'].status, 'ready');
  assert.equal(byId['plugin:websearch'].name, '联网搜索');
  assert.equal(byId['plugin:finance'].status, 'unavailable');
  assert.equal(byId['plugin:finance'].statusLabel, '启动失败');
  assert.equal(byId['plugin:document-index'].status, 'unavailable');
  assert.equal(byId['skill:meeting-notes'].status, 'ready');
  assert.equal(byId['skill:meeting-notes'].name, '会议整理');
  assert.equal(byId['skill:table-reconcile'].status, 'unavailable');
  assert.equal(byId['skill:table-reconcile'].statusLabel, '已停用');
  assert.equal(byId['skill:document-compare'].status, 'ready');
  assert.equal(byId['skill:harness-stock-research-v4'].name, '聊天投研 v4');
});

test('a failed candidate does not block a working active revision', () => {
  const library = model.buildCapabilityLibrary([], [], [
    {id: 'broken-candidate', enabled: true, active: null, pending: null,
      candidate_error: '候选包无效', history: [], blockers: []},
    {id: 'working-with-bad-candidate', enabled: true,
      active: {description: '当前可用版本'}, candidate_error: '候选包无效',
      pending: null, history: [], blockers: []},
  ]);
  const byId = Object.fromEntries(library.map((item) => [item.id, item]));
  assert.equal(byId['skill:broken-candidate'].status, 'unavailable');
  assert.equal(byId['skill:broken-candidate'].statusLabel, '候选包无效');
  assert.equal(byId['skill:working-with-bad-candidate'].status, 'ready');
});

test('visible selection is two chips plus a stable overflow count', () => {
  const six = Array.from({length: 6}, (_, index) => ({id: `skill:${index}`}));
  const result = model.visibleCapabilities(six, 2);
  assert.deepEqual(array(result.visible.map((item) => item.id)), ['skill:0', 'skill:1']);
  assert.equal(result.overflow, 4);
  assert.equal(model.visibleCapabilities([], 2).overflow, 0);
  assert.equal(model.visibleCapabilities(six.slice(0, 2), 2).overflow, 0);
});

test('search and kind filters compose', () => {
  const {plugins, packages, skills} = fixtures();
  const library = model.buildCapabilityLibrary(plugins, packages, skills);
  assert.deepEqual(
    array(model.filterCapabilities(library, '会议', 'all').map((item) => item.id)),
    ['skill:meeting-notes'],
  );
  assert.deepEqual(
    array(model.filterCapabilities(library, '插件', 'plugin').map((item) => item.id).sort()),
    ['plugin:document-index', 'plugin:finance', 'plugin:websearch'],
  );
  assert.deepEqual(
    array(model.filterCapabilities(library, 'websearch', 'skill')),
    [],
  );
  assert.equal(model.filterCapabilities(library, '', 'skill').length, 4);
});

test('toggle selection appends new ids, removes old ids and deduplicates', () => {
  assert.deepEqual(array(model.toggleSelection([], 'skill:a', true)), ['skill:a']);
  assert.deepEqual(array(model.toggleSelection(['skill:a'], 'skill:a', true)), ['skill:a']);
  assert.deepEqual(array(model.toggleSelection(['skill:a'], 'plugin:b', true)), ['skill:a', 'plugin:b']);
  assert.deepEqual(array(model.toggleSelection(['skill:a', 'plugin:b'], 'skill:a', false)), ['plugin:b']);
});

test('resolution drops unknown ids and preserves selection order', () => {
  const {plugins, packages, skills} = fixtures();
  const library = model.buildCapabilityLibrary(plugins, packages, skills);
  assert.deepEqual(
    array(model.resolveSelected(['skill:table-reconcile', 'missing', 'plugin:websearch'], library)
      .map((item) => item.id)),
    ['skill:table-reconcile', 'plugin:websearch'],
  );
  assert.deepEqual(
    array(model.resolvedSelectionIds(['skill:table-reconcile', 'missing'], library)),
    ['skill:table-reconcile'],
  );
});

const manager = load(new Map(), 'capabilityManagerModel');

test('manager maps display metadata, selected state and counts', () => {
  const {plugins, skills} = fixtures();
  const packages = [{
    id: 'local-tool', active: {
      revision: 'r1', source: 'C:\\pkg',
      manifest: {title: '本地工具', version: '1.2.0', description: '本地包', permissions: [], required: []},
    }, history: [], deployment: 'DEPLOYED',
  }];
  const removed = {
    id: 'old-skill', enabled: false, active: null, pending: null, history: [],
    blockers: [], uninstalled: true,
  };
  const items = manager.buildManagerItems(
    plugins, packages, [...skills, removed],
    ['plugin:websearch', 'skill:meeting-notes', 'skill:old-skill'],
  );
  const byId = Object.fromEntries(items.map((item) => [item.key, item]));
  assert.equal(byId['plugin:websearch'].name, '联网搜索');
  assert.equal(byId['plugin:websearch'].icon, 'i-wifi');
  assert.equal(byId['plugin:websearch'].source, '本机能力');
  assert.equal(byId['plugin:websearch'].origin, '内置');
  assert.equal(byId['plugin:websearch'].selected, true);
  assert.equal(byId['plugin:local-tool'].name, '本地工具');
  assert.equal(byId['plugin:local-tool'].source, '本地包');
  assert.equal(byId['plugin:local-tool'].origin, '用户导入');
  assert.equal(byId['plugin:local-tool'].version, '1.2.0');
  assert.equal(byId['skill:meeting-notes'].name, '会议整理');
  assert.equal(byId['skill:meeting-notes'].source, '当前工作区');
  assert.equal(byId['skill:meeting-notes'].origin, '工作区目录');
  assert.equal(byId['skill:meeting-notes'].selected, true);
  assert.equal(byId['skill:table-reconcile'].selected, false);
  assert.equal(byId['skill:old-skill'].removed, true);
  assert.equal(byId['skill:old-skill'].selected, false);

  const hidden = manager.managerCounts(items, false);
  assert.deepEqual({...hidden}, {all: 8, skill: 4, plugin: 4, enabled: 4, selected: 2, removed: 1});
  const shown = manager.managerCounts(items, true);
  assert.deepEqual({...shown}, {all: 9, skill: 5, plugin: 4, enabled: 4, selected: 2, removed: 1});
});

test('manager search, kind and history compose without changing counts', () => {
  const {plugins, skills} = fixtures();
  const items = manager.buildManagerItems(plugins, [], skills, []);
  assert.deepEqual(
    array(manager.filterManagerItems(items, '会议', 'all', false).map((item) => item.key)),
    ['skill:meeting-notes'],
  );
  assert.equal(manager.filterManagerItems(items, '', 'skill', false).length, 4);
  assert.equal(manager.filterManagerItems(items, '', 'plugin', false).length, 3);
  assert.equal(manager.filterManagerItems(items, '本机能力', 'plugin', false).length, 3);
  assert.equal(manager.filterManagerItems(items, '不存在的关键词', 'all', false).length, 0);
  const counts = manager.managerCounts(items, false);
  assert.equal(counts.all, 7);
  assert.equal(counts.selected, 0);
});

test('recent sanitization and ordering cap at three and stay stable', () => {
  assert.deepEqual(array(model.sanitizeRecent(null)), []);
  assert.deepEqual(array(model.sanitizeRecent(['a', 'a', 7, 'b', 'c', 'd'])), ['a', 'b', 'c']);
  assert.deepEqual(array(model.rememberRecent(['a', 'b'], 'a')), ['a', 'b']);
  assert.deepEqual(array(model.rememberRecent(['a', 'b', 'c'], 'd')), ['d', 'a', 'b']);
  const {plugins, packages, skills} = fixtures();
  const library = model.buildCapabilityLibrary(plugins, packages, skills);
  const sorted = model.sortByRecent(library, ['plugin:websearch', 'skill:meeting-notes']);
  assert.deepEqual(array(sorted.slice(0, 2).map((item) => item.id)),
    ['plugin:websearch', 'skill:meeting-notes']);
  const rest = array(sorted.slice(2).map((item) => item.id));
  const original = array(library.map((item) => item.id).filter((id) => !['plugin:websearch', 'skill:meeting-notes'].includes(id)));
  assert.deepEqual(rest, original);
});
