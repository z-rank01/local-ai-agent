import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
import {build} from 'esbuild';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const module={exports:{}};
const source=readFileSync(new URL('../src/lib/reportDocument.ts',import.meta.url),'utf8');
vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,{module,exports:module.exports});
const {checkedReportPage}=module.exports;
const doc=()=>({layout:'research-reader-v2',revision:'a'.repeat(64),section:'body',page:1,page_count:1,
  content:'正文',blocks:[{id:'judgment',kind:'markdown',content:'正文'}]});
test('typed body and separate reference pages retain all Markdown',()=>{
  const v=doc();v.section='references';v.blocks=[{id:'stage-1',kind:'disclosure',title:'趋势分析 · 阶段1',content:'# 标题\n\n- 末尾反证'}];
  assert.equal(checkedReportPage(v,1,'references').blocks[0].content,'# 标题\n\n- 末尾反证');
});
test('wrong section/page/revision or arbitrary component kind cannot replace visible report',()=>{
  for (const change of [{section:'references'},{page:2},{revision:'bad'},
    {blocks:[{id:'injected',kind:'html',content:'<script>run()</script>'}]},
    {blocks:[{id:'missing-title',kind:'disclosure',content:'正文'}]}]) {
    assert.throws(()=>checkedReportPage({...doc(),...change},1,'body'));
  }
});
test('legacy pages continue to render without a new document schema',()=>{
  assert.equal(checkedReportPage({page:2,page_count:3,content:'旧正文'},2,'body').content,'旧正文');
});

test('shared Markdown renders headings lists quotes tables and code without arbitrary HTML',async()=>{
  const built=await build({stdin:{contents:`
    import React from 'react';
    import {renderToStaticMarkup} from 'react-dom/server';
    import {Markdown} from './src/ui/Markdown';
    import {AssistantTurn} from './src/app/AssistantTurn';
    export function renderTurn(blocks) {
      return renderToStaticMarkup(React.createElement(BriefReaderContext.Provider,{value:()=>{}},
        React.createElement(AssistantTurn,{item:{id:'turn',blocks},busy:false,onReadReport:()=>{}})));
    }
    import {BriefReaderContext} from './src/lib/briefReader';
    export {workspaceDocumentKey,workspaceDocumentKind,prepareWorkspaceDocument} from './src/lib/workspaceDocument';
    export {structuredWorkspaceDocumentPaths} from './src/lib/report';
    export function renderBrief(content,suppressed=[],previous=[]) {
      return renderToStaticMarkup(React.createElement(BriefReaderContext.Provider,{value:()=>{}},
        React.createElement(Markdown,{content,suppressedDocumentPaths:new Set(suppressed),precedingDocumentContents:previous})));
    }
    export function render(content) {
      return renderToStaticMarkup(React.createElement(Markdown,{content,compactHeadings:true,onSourceReference:()=>{}}));
    }`,resolveDir:fileURLToPath(new URL('../',import.meta.url)),loader:'tsx'},
    bundle:true,write:false,format:'cjs',platform:'node',loader:{'.css':'empty'},define:{'import.meta.env':'{}'}});
  const renderedModule={exports:{}};
  vm.runInNewContext(built.outputFiles[0].text,{module:renderedModule,exports:renderedModule.exports,
    require:createRequire(import.meta.url),process,console,TextEncoder,TextDecoder,URL,URLSearchParams,setTimeout,clearTimeout,
    window:{location:{hash:''},localStorage:{getItem:()=>null}}});
  const html=renderedModule.exports.render('# 标题\n\n- 依据\n\n> 末尾反证\n\n| 指标 | 值 |\n| --- | --- |\n| 原值 | 1234567890123456 |\n\n```text\n完整原文\n```\n\n[资料1](#report-source-1)\n\n<script>alert(1)</script>');
  for(const expected of ['<h3>标题</h3>','<li>依据</li>','<blockquote>','<table>','1234567890123456','code-card','完整原文','report-source-link']) {
    assert.ok(html.includes(expected),expected);
  }
  assert.ok(!html.includes('<script>'));
  const brief=renderedModule.exports.renderBrief('[样稿](/workspace/reports/v3/report.html)\n\n[外部](https://example.com/report.html)');
  assert.ok(brief.includes('class="report-card"'));
  assert.ok(brief.includes('>阅读简报</button>'));
  assert.ok(brief.includes('href="https://example.com/report.html"'));
  const titled=renderedModule.exports.renderBrief('阅读简报：[实际报告标题](/workspace/reports/report.html)');
  assert.ok(titled.includes('class="report-card"'));
  assert.ok(titled.includes('实际报告标题'));
  assert.ok(!renderedModule.exports.renderBrief('正文引用：[资料](/workspace/reports/report.html)').includes('class="report-card"'));
  assert.ok(!renderedModule.exports.render('[下载](/workspace/report.html)').includes('class="report-card"'));

  const markdown=renderedModule.exports.renderBrief('阅读文档：[研究笔记](/workspace/reports/note.md)');
  assert.ok(markdown.includes('Markdown 文档') || markdown.includes('研究笔记'));
  assert.ok(markdown.includes('note.md'));
  assert.ok(markdown.includes('>阅读文档</button>'));
  const repeated = '[first](/workspace/a.md)\n\n[second](/workspace/a.md)';
  assert.equal((renderedModule.exports.renderBrief(repeated).match(/class="report-card"/g)||[]).length,1);
  assert.equal((renderedModule.exports.renderBrief('[second](/workspace/a.md)',[],['[first](/workspace/a.md)']).match(/class="report-card"/g)||[]).length,0);
  assert.equal((renderedModule.exports.renderBrief(repeated).match(/class="report-card"/g)||[]).length,1);
  const block=(id,path)=>({id,kind:'tool',label:'brief_publish',status:'completed',messageId:id,toolResult:{status:'PUBLISHED',path}});
  const turn=renderedModule.exports.renderTurn([block('a','/workspace/a.md'),block('b','/workspace/a.md'),block('c','/workspace/b.md')]);
  assert.equal((turn.match(/正在读取成果信息/g)||[]).length,2);
  const links=[{id:'a',kind:'assistant',text:'[first](/workspace/a.md)'},{id:'b',kind:'assistant',text:'[second](/workspace/a.md)'}];
  assert.equal((renderedModule.exports.renderTurn(links).match(/class="report-card"/g)||[]).length,1);
  assert.equal((renderedModule.exports.renderTurn(links).match(/class="report-card"/g)||[]).length,1);
  const duplicatePath='/workspace/reports/report.html';
  assert.ok(!renderedModule.exports.renderBrief('阅读简报：[同一份报告](/workspace/reports/report.html)',[duplicatePath]).includes('class="report-card"'));
  const different=renderedModule.exports.renderBrief('[另一份报告](/workspace/reports/other.html)',[duplicatePath]);
  assert.ok(different.includes('class="report-card"'));
  assert.ok(different.includes('另一份报告'));
  const inline=renderedModule.exports.renderBrief('正文引用：[资料](/workspace/reports/report.html)',[duplicatePath]);
  assert.ok(!inline.includes('class="report-card"'));
  assert.ok(inline.includes('正文引用：'));
  assert.ok(!renderedModule.exports.renderBrief('[越界](/workspace/../secret.html)').includes('class="report-card"'));

  assert.equal(renderedModule.exports.workspaceDocumentKind('/workspace/a.html'),'html');
  assert.equal(renderedModule.exports.workspaceDocumentKind('/roots/main/b.markdown'),'markdown');
  assert.equal(renderedModule.exports.workspaceDocumentKind('/workspace/a.pdf'),null);
  assert.equal(renderedModule.exports.workspaceDocumentKey('/workspace/a%20b.md'),'/workspace/a b.md');
  assert.equal(renderedModule.exports.workspaceDocumentKey('/workspace/a.md?x=1'),'');
  const prepared=renderedModule.exports.prepareWorkspaceDocument({name:'note.md',path:'/workspace/note.md',content:'# 正文',is_binary:false,truncated:false},'default');
  assert.equal(prepared.kind,'markdown');assert.equal(prepared.content,'# 正文');assert.equal(prepared.title,'note.md');
  assert.throws(()=>renderedModule.exports.prepareWorkspaceDocument({name:'note.md',path:'/workspace/note.md',content:'# 正文',is_binary:false,truncated:true},'default'));
  const paths=renderedModule.exports.structuredWorkspaceDocumentPaths([
    {label:'brief_publish',messageId:'m1',toolResult:{status:'PUBLISHED',path:duplicatePath}},
    {label:'brief_publish',messageId:'m2',toolResult:{status:'PUBLISHED',path:'/workspace/note.md'}},
    {label:'brief_publish',messageId:'m3',toolResult:{status:'PUBLISHED',path:'/workspace/../secret.html'}},
    {label:'other',messageId:'m4',toolResult:{status:'PUBLISHED',path:'/workspace/other.html'}},
  ]);
  assert.deepEqual([...paths].sort(),['/workspace/note.md',duplicatePath].sort());
});
