import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const cache = new Map();
function load(name) {
  if (cache.has(name)) return cache.get(name);
  const module = {exports: {}};
  cache.set(name, module.exports);
  const source = readFileSync(new URL(`../src/lib/${name}.ts`, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText;
  vm.runInNewContext(code, {module, exports: module.exports,
    require: spec => spec.startsWith('./') ? load(spec.slice(2)) : require(spec)});
  return module.exports;
}
const {assistantGroupStatus, buildAssistantTimeline, buildBlocksFromMessages} = load('transcript');

test('stored recovered tool failure remains visible without marking final answer failed', () => {
  const blocks = buildBlocksFromMessages([
    {id: '1', role: 'tool', tool_name: 'stock_report_read', content: '[error] invalid page'},
    {id: '2', role: 'tool', tool_name: 'stock_report_read', content: '[ok] report'},
    {id: '3', role: 'assistant', status: 'ok', content: '研究已入队，尚未完成。'},
  ]);
  assert.equal(assistantGroupStatus(blocks), undefined);
  const steps = buildAssistantTimeline(blocks, assistantGroupStatus(blocks), 1, 1);
  assert.equal(steps[0].status, 'error');
  assert.ok(steps[0].badges.includes('失败节点'));
  assert.equal(steps[2].status, 'ok');
});

test('interrupted final answer and unfinished failed tools remain errors', () => {
  assert.equal(assistantGroupStatus(buildBlocksFromMessages([
    {id: '1', role: 'assistant', content: '部分成果', status: 'interrupted'},
  ])), 'error');
  assert.equal(assistantGroupStatus([
    {kind: 'assistant', status: 'ok'}, {kind: 'tool', status: 'error'},
  ]), 'error');
});

test('active generation takes precedence over prior successful answer', () => {
  assert.equal(assistantGroupStatus([
    {kind: 'tool', status: 'running'}, {kind: 'assistant', status: 'ok'},
  ]), 'running');
});
