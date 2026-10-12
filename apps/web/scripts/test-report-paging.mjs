import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
function load(file, dependencies = {}) {
  const source=readFileSync(new URL(file,import.meta.url),'utf8');
  const module={exports:{}};
  vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,
    {module,exports:module.exports,require:name=>dependencies[name]});
  return module.exports;
}
const {reportPageContent}=load('../src/lib/reportPaging.ts');
const {buildReportCardInfo}=load('../src/lib/report.ts',{'./format':{formatTime:v=>v}});
test('navigation footer is removed while tail counterevidence remains',()=>{
  const body='判断\n<details><summary>未完成范围与完整缺口记录</summary>\n末尾反证\n</details>';
  assert.equal(reportPageContent(body+'\n\n---\n\n第 2/5 页。可继续说“查看刚才的报告第 3 页”。'),body);
  assert.equal(reportPageContent(body+'\n第2/5页是原文'),body+'\n第2/5页是原文');
});
test('report card binds persisted tool message and uses authoritative footer for older totals',()=>{
  const info=buildReportCardInfo({messageId:'saved-tool',params:{page:2},
    toolResult:{model_observation:{page_count:1},local_result:'原文\n\n---\n\n第 2/5 页。可继续说下一页'}});
  assert.equal(info.sourceMessageId,'saved-tool');assert.equal(info.page,2);assert.equal(info.pageCount,5);
});
test('legacy card with no message reference keeps its saved content',()=>{
  const info=buildReportCardInfo({toolResult:{local_result:'旧页'},params:{page:1}});
  assert.equal(info.sourceMessageId,undefined);assert.equal(info.content,'旧页');
});
