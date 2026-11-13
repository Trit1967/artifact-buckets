// A small, safe Markdown renderer for previews. Everything is HTML-escaped first, then a fixed set of
// Markdown forms is turned into tags. Raw HTML in the file is shown as text, never run.

const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
// Exact inverse of esc: &amp; last, so "&amp;lt;" comes back as "&lt;", not "<".
const unesc = (s) => s.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&amp;/g, '&');

function safeHref(u) {
  const url = unesc(u.trim());
  if (/^(https?:|mailto:|#|\/(?!\/))/i.test(url)) return esc(url);
  return null;
}

function emphasis(s) {
  s = s.replace(/\*\*([^*\s](?:[^*]*[^*\s])?)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|\W)__([^_\s](?:[^_]*[^_\s])?)__(?=\W|$)/g, '$1<strong>$2</strong>');
  s = s.replace(/(^|[^*\w])\*([^*\s](?:[^*]*[^*\s])?)\*(?!\w)/g, '$1<em>$2</em>')
    .replace(/(^|\W)_([^_\s](?:[^_]*[^_\s])?)_(?=\W|$)/g, '$1<em>$2</em>');
  return s.replace(/~~([^~]+)~~/g, '<del>$1</del>');
}

function inline(s) {
  // s is already escaped. Code spans and links are set aside first, so emphasis never runs over them.
  const held = [];
  const hold = (html) => { held.push(html); return `\u0000${held.length - 1}\u0000`; };
  s = s.replace(/`([^`]+)`/g, (_, c) => hold(`<code>${c}</code>`));
  s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)[^)]*\)/g, (_, alt) => hold(`<span class="md-img">[image: ${alt || 'untitled'}]</span>`));
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)[^)]*\)/g, (m, text, href) => {
    const h = safeHref(href);
    return h ? hold(`<a href="${h}" target="_blank" rel="noopener noreferrer">${emphasis(text)}</a>`) : text;
  });
  s = emphasis(s);
  for (let n = 0; n < 3 && s.includes('\u0000'); n++) s = s.replace(/\u0000(\d+)\u0000/g, (_, i) => held[+i]);
  return s;
}

function table(lines) {
  const cells = (l) => l.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map((c) => inline(c.trim()));
  const head = cells(lines[0]);
  const body = lines.slice(2).map(cells);
  return `<table><thead><tr>${head.map((c) => `<th>${c}</th>`).join('')}</tr></thead><tbody>${
    body.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

export function markdown(src) {
  const lines = esc(String(src).replace(/\r\n?/g, '\n').replace(/\u0000/g, '')).split('\n');
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    let m;
    if ((m = /^\s*(```|~~~)\s*([\w+-]*)/.exec(l))) {
      const fence = m[1]; const buf = []; i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence)) buf.push(lines[i++]);
      i++;
      out.push(`<pre><code${m[2] ? ` data-lang="${m[2]}"` : ''}>${buf.join('\n')}</code></pre>`);
      continue;
    }
    if ((m = /^(#{1,6})\s+(.*?)\s*#*\s*$/.exec(l))) { out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`); i++; continue; }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(l)) { out.push('<hr>'); i++; continue; }
    if (/^\s*\|.*\|\s*$/.test(l) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      const buf = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) buf.push(lines[i++]);
      out.push(table(buf));
      continue;
    }
    if (/^\s*&gt;\s?/.test(l)) {
      const buf = [];
      while (i < lines.length && /^\s*&gt;\s?/.test(lines[i])) buf.push(lines[i++].replace(/^\s*&gt;\s?/, ''));
      out.push(`<blockquote>${markdown(unesc(buf.join('\n')))}</blockquote>`);
      continue;
    }
    if ((m = /^\s*([-*+]|\d+[.)])\s+/.exec(l))) {
      const ordered = /\d/.test(m[1]);
      const buf = [];
      while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
        let item = lines[i++].replace(/^\s*([-*+]|\d+[.)])\s+/, '');
        let box = '';
        const t = /^\[([ xX])\]\s+/.exec(item);
        if (t) { box = `<span class="md-box${t[1] === ' ' ? '' : ' on'}"></span>`; item = item.slice(t[0].length); }
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) item += ' ' + lines[i++].trim();
        buf.push(`<li${box ? ' class="task"' : ''}>${box}${inline(item)}</li>`);
      }
      out.push(ordered ? `<ol>${buf.join('')}</ol>` : `<ul>${buf.join('')}</ul>`);
      continue;
    }
    if (!l.trim()) { i++; continue; }
    const buf = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|\s*(```|~~~)|\s*&gt;|\s*([-*+]|\d+[.)])\s+|\s*\|.*\|\s*$)/.test(lines[i])) buf.push(lines[i++].trim());
    if (!buf.length) buf.push(lines[i++].trim());
    out.push(`<p>${inline(buf.join(' '))}</p>`);
  }
  return out.join('\n');
}

// CSV to rows, handling quoted fields. Capped so a huge file can't freeze the page.
export function csv(text, maxRows = 500) {
  const rows = [];
  let row = [], field = '', q = false;
  for (let i = 0; i < text.length && rows.length < maxRows; i++) {
    const c = text[i];
    if (q) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; } else if (c === '"') q = false; else field += c;
    } else if (c === '"') q = true;
    else if (c === ',') { row.push(field); field = ''; }
    else if (c === '\n') { row.push(field); rows.push(row); row = []; field = ''; }
    else if (c !== '\r') field += c;
  }
  if (field || row.length) row.push(field), rows.push(row);
  return rows;
}
