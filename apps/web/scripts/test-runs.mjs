import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';
const module={exports:{}};
vm.runInNewContext(ts.transpileModule(readFileSync(new URL('../src/lib/runs.ts',import.meta.url),'utf8'),
  {compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,{module,exports:module.exports});
const {conversationRun,acceptRunEvent,shouldObserveRun}=module.exports;

test('a script finishing between polls still synchronizes its final messages exactly once',()=>{
  const finished={id:'script',status:'SUCCEEDED',last_seq:42};
  assert.equal(shouldObserveRun(finished,{}),true);
  assert.equal(shouldObserveRun(finished,{script:42}),false);
  assert.equal(shouldObserveRun({...finished,last_seq:43},{script:42}),true);
  assert.equal(shouldObserveRun({...finished,status:'RUNNING'},{script:42}),true);
  assert.equal(shouldObserveRun(undefined,{}),false);
});
test('a continued live execution wins over its recently updated parent',()=>{
  const parent={id:'parent',conversation_id:'a',status:'STOPPED'};
  const child={id:'child',conversation_id:'a',status:'RUNNING'};
  assert.equal(conversationRun([parent,child],'a').id,'child');
  assert.equal(conversationRun([parent,child],'b'),undefined);
  assert.equal(conversationRun([parent,child],null),undefined);
});
test('duplicate reconnect events cannot append deltas or steps twice',()=>{
  const cursors={};
  assert.equal(acceptRunEvent(cursors,{run_id:'a',seq:1}),true);
  assert.equal(acceptRunEvent(cursors,{run_id:'a',seq:1}),false);
  assert.equal(acceptRunEvent(cursors,{run_id:'a',seq:0}),false);
  assert.equal(acceptRunEvent(cursors,{run_id:'b',seq:1}),true);
  assert.equal(acceptRunEvent(cursors,{run_id:'a',seq:null}),true);
  assert.equal(cursors.a,1);
});
