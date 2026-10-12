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
});
