export function bytes(n, digits = 1) {
  if (n === null || n === undefined) return '—';
  const u = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let i = 0;
  let v = Number(n);
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${i === 0 ? v : v.toFixed(v >= 100 ? 0 : digits)} ${u[i]}`;
}

export function ago(iso) {
  if (!iso) return 'Never';
  const t = new Date(iso).getTime();
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 45) return 'Just now';
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h`;
  const d = Math.round(h / 24);
  if (d === 1) return 'Yesterday';
  if (d < 30) return `${d} days`;
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

export function when(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

export function initials(name) {
  const p = String(name || '?').replace(/[._-]+/g, ' ').trim().split(/\s+/);
  return ((p[0]?.[0] || '?') + (p.length > 1 ? p[p.length - 1][0] : '')).toUpperCase();
}

// Stable colour pair for a person or group, from a small palette that reads well on the dark UI.
const PAIRS = [['#1E2A24', '#4CC38A'], ['#1F2235', '#8AA4FF'], ['#2A1F2E', '#D59BE6'], ['#2A2214', '#F2A93B'], ['#2E1D18', '#E5765C'], ['#16282B', '#5CC8D0']];
export function tint(key) {
  let h = 0;
  for (const c of String(key || '')) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return PAIRS[h % PAIRS.length];
}

export function ext(name) {
  const m = /\.([a-z0-9]{1,5})$/i.exec(name || '');
  return m ? m[1].toLowerCase() : '';
}

const EXT_TINT = { html: ['#2A2214', '#F2A93B'], htm: ['#2A2214', '#F2A93B'], md: ['#1F2235', '#8AA4FF'], json: ['#1E2A24', '#4CC38A'],
  csv: ['#1E2A24', '#4CC38A'], svg: ['#2A1F2E', '#D59BE6'], png: ['#2A1F2E', '#D59BE6'], jpg: ['#2A1F2E', '#D59BE6'], pdf: ['#2E1D18', '#E5765C'],
  py: ['#16282B', '#5CC8D0'], js: ['#2A2214', '#F2A93B'], ts: ['#1F2235', '#8AA4FF'], sql: ['#16282B', '#5CC8D0'] };
export function extTint(name) { return EXT_TINT[ext(name)] || ['#1C1E22', '#C4C8CE']; }

export const SOURCE = { mcp: ['Claude Code', '#F2A93B'], web: ['Upload', '#8AA4FF'], api: ['API', '#5CC8D0'], smb: ['Windows share', '#D59BE6'] };

export function crumbs(path) {
  const parts = path.split('/').filter(Boolean);
  return parts.map((p, i) => ({ name: p, path: '/' + parts.slice(0, i + 1).join('/') }));
}

/** Re-indent JSON without parsing values, so numbers, escapes and key order stay exactly as written.
 *  Returns the input unchanged when it isn't valid JSON. */
export function prettyJson(t) {
  try { JSON.parse(t); } catch { return t; }
  let out = '', depth = 0, inStr = false, esc = false;
  const nl = () => '\n' + '  '.repeat(depth);
  for (let i = 0; i < t.length; i++) {
    const c = t[i];
    if (inStr) { out += c; if (esc) esc = false; else if (c === '\\') esc = true; else if (c === '"') inStr = false; continue; }
    if (c === '"') { inStr = true; out += c; continue; }
    if (c === ' ' || c === '\n' || c === '\r' || c === '\t') continue;
    if (c === '{' || c === '[') {
      let j = i + 1; while (/\s/.test(t[j] || '')) j++;
      if (t[j] === (c === '{' ? '}' : ']')) { out += c + t[j]; i = j; continue; }
      depth++; out += c + nl(); continue;
    }
    if (c === '}' || c === ']') { depth--; out += nl() + c; continue; }
    if (c === ',') { out += ',' + nl(); continue; }
    if (c === ':') { out += ': '; continue; }
    out += c;
  }
  return out;
}
