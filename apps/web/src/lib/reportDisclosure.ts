export type ReportPart = {kind: 'markdown'; content: string}
  | {kind: 'disclosure'; title: string; content: string};

const TITLES = new Set(['研究进度、阶段原文与执行记录', '未完成范围与完整缺口记录']);

// Recognize only renderer-owned, attribute-free wrappers. All contents still go
// through ReactMarkdown's ordinary escaping; arbitrary HTML is never enabled.
export function splitReportDisclosures(content: string): ReportPart[] {
  const lines = content.replace(/\r\n/g, '\n').split('\n');
  const parts: ReportPart[] = [];
  let plain: string[] = [];
  let fence = '';
  let fenceLength = 0;
  function updateFence(line: string): void {
    const match = /^ {0,3}(`{3,}|~{3,})/.exec(line);
    if (!match) return;
    const char = match[1][0];
    if (!fence) { fence = char; fenceLength = match[1].length; }
    else if (char === fence && match[1].length >= fenceLength
      && new RegExp(`^ {0,3}${char}{${fenceLength},}\\s*$`).test(line)) fence = '';
  }
  for (let i = 0; i < lines.length; i += 1) {
    const start = !fence && /^<details><summary>([^<>]+)<\/summary>$/.exec(lines[i]);
    if (start && TITLES.has(start[1])) {
      const body: string[] = [];
      let end = i + 1;
      let valid = false;
      for (; end < lines.length; end += 1) {
        if (!fence && lines[end].endsWith('</details>')) {
          const tail = lines[end].slice(0, -'</details>'.length);
          if (tail) body.push(tail);
          valid = true;
          break;
        }
        if (!fence && lines[end].startsWith('<details>')) break;
        body.push(lines[end]); updateFence(lines[end]);
      }
      if (valid) {
        if (plain.length) parts.push({kind: 'markdown', content: plain.join('\n')});
        plain = [];
        parts.push({kind: 'disclosure', title: start[1], content: body.join('\n')});
        i = end;
        continue;
      }
      // Malformed wrappers remain visible; never swallow a tail of evidence.
      fence = ''; fenceLength = 0;
    }
    plain.push(lines[i]); updateFence(lines[i]);
  }
  if (plain.length) parts.push({kind: 'markdown', content: plain.join('\n')});
  return parts;
}
