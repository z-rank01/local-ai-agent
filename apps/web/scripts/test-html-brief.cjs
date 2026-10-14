// NODE_PATH may point to the existing bundled Playwright runtime; no server is started.
const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const assert = require('node:assert/strict');
const ts = require('typescript');
const {chromium} = require('playwright');

(async () => {
  const browser = await chromium.launch({headless: true, executablePath: process.env.BRIEF_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  try {
    const page = await browser.newPage();
    const source = readFileSync(resolve(__dirname, '../src/lib/htmlBrief.ts'), 'utf8');
    const js = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020}}).outputText;
    await page.addScriptTag({content: 'var exports = {};\n' + js});
    const result = await page.evaluate(() => {
      const api = exports;
      const file = {name:'report.html',path:'/workspace/reports/report.html',content:'<title>简报</title><meta name="brief-edition" content="v3"><style>p{color:red}</style><script>window.compromised=1</script><img src="https://invalid.test/leak"><svg onload="alert(1)"><text>数值</text><foreignObject>bad</foreignObject></svg><a href="javascript:alert(1)">bad</a><a href="https://example.com">来源</a><section class="references"><p id="s1">依据</p></section><meta http-equiv="refresh" content="0;url=https://invalid.test">', is_binary:false,truncated:false};
      const parsed = api.prepareHtmlBrief(file,'named-workspace');
      const doc = new DOMParser().parseFromString(parsed.html,'text/html');
      const rejects = [{...file,truncated:true},{...file,is_binary:true},{...file,content:''},{...file,path:'/workspace/../secret.html'}].every(f => {try {api.prepareHtmlBrief(f,'x');return false;} catch {return true;}});
      return {rejects,workspace:parsed.workspaceId,meta:parsed.meta,
        paths: ['/workspace/report.html','/roots/root1/report.html','/outside/report.html','/workspace/a.html?x=1'].map(api.isWorkspaceHtml),
        unsafe:doc.querySelectorAll('script,img,foreignObject,[onload],meta[http-equiv="refresh"]').length,
        jsLinks:[...doc.querySelectorAll('a')].some(a=>a.getAttribute('href')?.startsWith('javascript:')),
        csp:doc.querySelector('meta[http-equiv="Content-Security-Policy"]').content,
        details:!!doc.querySelector('.references details #s1'), text:doc.querySelector('svg text').textContent};
    });
    assert.equal(result.rejects,true); assert.equal(result.unsafe,0); assert.equal(result.jsLinks,false);
    assert.deepEqual(result.paths,[true,true,false,false]); assert.equal(result.workspace,'named-workspace');
    assert.deepEqual(result.meta,['v3']); assert.equal(result.details,true); assert.equal(result.text,'数值');
    assert.match(result.csp,/script-src 'none'/);
    // A real browser must enforce the inert frame even for retained CSS URLs.
    const requests=[]; await page.route('https://invalid.test/**',route=>{requests.push(route.request().url());return route.abort();});
    await page.evaluate(() => {
      const f=document.createElement('iframe');f.sandbox='allow-same-origin allow-popups allow-popups-to-escape-sandbox';
      f.srcdoc=exports.prepareHtmlBrief({name:'x.html',path:'/workspace/x.html',is_binary:false,truncated:false,content:'<style>@import "https://invalid.test/style";body{background:url(https://invalid.test/bg)}</style><script>parent.compromised=1</script><p>安全正文</p>'},'default').html;
      document.body.append(f);
    });
    await page.frameLocator('iframe').getByText('安全正文').waitFor();
    assert.equal(await page.evaluate(()=>window.compromised),undefined); assert.deepEqual(requests,[]);
    console.log('HTML brief: permission identity, complete read, inert content, references and network isolation passed');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
