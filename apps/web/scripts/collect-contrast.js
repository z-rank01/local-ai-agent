/** Read-only browser audit; run against the real BFF page after each UI state. */
export function collectContrast() {
  const parse = value => {
    // CSS Color 4: color-mix() can serialize to Oklab, not rgb().
    const lab = value.match(/^oklab\(([^)]+)\)$/);
    if (lab) {
      const [l, a, b, alpha = 1] = lab[1].split(/[ /]+/).filter(Boolean).map(Number);
      const x = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3;
      const y = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3;
      const z = (l - 0.0894841775 * a - 1.291485548 * b) ** 3;
      const linear = [4.0767416621 * x - 3.3077115913 * y + 0.2309699292 * z,
        -1.2684380046 * x + 2.6097574011 * y - 0.3413193965 * z,
        -0.0041960863 * x - 0.7034186147 * y + 1.707614701 * z];
      return [...linear.map(c => Math.min(1, Math.max(0, c <= 0.0031308 ? c * 12.92 : 1.055 * c ** (1 / 2.4) - 0.055)) * 255), alpha];
    }
    const match = value.match(/^rgba?\(([^)]+)\)$/);
    if (!match) return null;
    const parts = match[1].split(/[, /]+/).filter(Boolean).map(Number);
    return [parts[0], parts[1], parts[2], parts[3] ?? 1];
  };
  const composite = (front, back) => front.slice(0, 3).map((c, i) => c * front[3] + back[i] * (1 - front[3]));
  const luminance = rgb => rgb.map(c => c / 255).map(c => c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)
    .reduce((sum, c, i) => sum + c * [0.2126, 0.7152, 0.0722][i], 0);
  const background = element => {
    const chain = [];
    for (let node = element; node; node = node.parentElement) chain.push(node);
    let value = [255, 255, 255];
    for (const node of chain.reverse()) {
      const color = parse(getComputedStyle(node).backgroundColor);
      if (color) value = composite(color, value);
    }
    return value;
  };
  const results = [];
  const unparsed = [];
  const modals = Array.from(document.querySelectorAll('[role="dialog"][aria-modal="true"]')).filter(e => getComputedStyle(e).visibility !== 'hidden' && e.getBoundingClientRect().height);
  const z = element => { let score = 0; for (let n = element; n; n = n.parentElement) score = Math.max(score, Number.parseInt(getComputedStyle(n).zIndex, 10) || 0); return score; };
  const modal = modals.sort((a, b) => z(b) - z(a))[0];
  for (const element of document.querySelectorAll('*')) {
    if (modal && !modal.contains(element)) continue;
    const icon = element.matches('svg.ico');
    const text = !icon && Array.from(element.childNodes).some(node => node.nodeType === 3 && node.textContent.trim());
    if (!icon && !text) continue;
    if (element.closest('[aria-hidden="true"],[hidden],[inert],[disabled],[aria-disabled="true"]')) continue;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    if (!rect.width || !rect.height || rect.bottom <= 0 || rect.top >= innerHeight || rect.right <= 0 || rect.left >= innerWidth || style.visibility === 'hidden') continue;
    const color = parse(style.color);
    if (!color) { unparsed.push(style.color); continue; }
    let opacity = 1;
    for (let node = element; node; node = node.parentElement) opacity *= Number(getComputedStyle(node).opacity);
    if (!opacity) continue;
    color[3] *= opacity;
    const bg = background(element);
    const fg = composite(color, bg);
    const a = luminance(fg), b = luminance(bg);
    const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    const size = Number.parseFloat(style.fontSize), weight = Number.parseInt(style.fontWeight, 10);
    const threshold = icon || size >= 24 || (size >= 18.66 && weight >= 700) ? 3 : 4.5;
    results.push({kind: icon ? 'icon' : 'text', tag: element.tagName, class: element.getAttribute('class') ?? '', color: style.color,
      background: bg.map(Math.round), ratio: Math.round(ratio * 100) / 100, threshold, passed: ratio >= threshold});
  }
  let focus = null;
  const active = document.activeElement;
  if (active?.closest('.theme-options') && active.matches(':focus-visible')) {
    const shadow = getComputedStyle(active).boxShadow;
    const colors = shadow.match(/(?:rgba?|oklab)\([^)]+\)/g) ?? [];
    const ring = parse(colors[colors.length - 1] ?? '');
    if (ring) {
      const bg = background(active.parentElement), painted = composite(ring, bg);
      const a = luminance(painted), b = luminance(bg);
      focus = {shadow, ratio: Math.round((Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05) * 100) / 100, threshold: 3};
    }
  }
  return {theme: document.documentElement.dataset.theme ?? 'light', count: results.length, focus,
    minimum: Math.min(...results.map(result => result.ratio)), failures: results.filter(result => !result.passed), unparsed: [...new Set(unparsed)], results};
}
