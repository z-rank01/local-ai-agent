import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
const source = readFileSync(new URL('../src/lib/reportDisclosure.ts', import.meta.url), 'utf8');
const module = {exports: {}};
vm.runInNewContext(ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText,
  {module, exports: module.exports});
const {splitReportDisclosures: split} = module.exports;
const title = '研究进度、阶段原文与执行记录';
const wrapper = body => `<details><summary>${title}</summary>\n\n${body}\n\n</details>`;

test('keeps primary conclusions outside collapsed trace and preserves every source', () => {
  const parts = split('结论：订单尚未签合同\n' + wrapper('来源 abc123\n末尾反证：已撤销') + '\n后续观察');
  assert.equal(parts.length, 3);
  assert.equal(parts[1].kind, 'disclosure');
  assert.ok(parts[1].content.includes('末尾反证：已撤销'));
  assert.ok(parts[0].content.includes('结论'));assert.ok(parts[2].content.includes('后续观察'));
});
test('never interprets arbitrary HTML, attributes or unfamiliar summaries', () => {
  for (const text of ['<details onclick="alert(1)"><summary>研究进度、阶段原文与执行记录</summary>\n资料\n</details>',
    '<details><summary>未知标题</summary>\n资料\n</details>', '<script>alert(1)</script>']) {
    assert.ok(split(text).every(p => p.kind === 'markdown'));
  }
});
test('wrappers in fences are literal; closing tags in fenced evidence cannot swallow the tail', () => {
  const literal = '```html\n' + wrapper('资料') + '\n```';
  assert.ok(split(literal).every(p => p.kind === 'markdown'));
  const parts = split(wrapper('```text\n</details>\n```\n末尾原文'));
  assert.equal(parts[0].kind, 'disclosure');assert.ok(parts[0].content.includes('末尾原文'));
});
test('incomplete or nested wrappers preserve all text instead of hiding evidence', () => {
  for (const content of [`<details><summary>${title}</summary>\n未闭合末尾`, wrapper(wrapper('嵌套资料'))]) {
    assert.ok(split(content).map(p => p.content).join('\n').includes(content.includes('末尾') ? '未闭合末尾' : '嵌套资料'));
  }
});
test('a paginated long line keeps its final characters before the closing wrapper', () => {
  const parts = split(`<details><summary>${title}</summary>\n本页原文末尾</details>\n`);
  assert.equal(parts[0].kind, 'disclosure');
  assert.equal(parts[0].content, '本页原文末尾');
});
