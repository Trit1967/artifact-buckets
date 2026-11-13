export const toasts = $state({ items: [] });
let n = 0;
export function toast(text, kind = 'ok') {
  const id = ++n;
  toasts.items.push({ id, text, kind });
  setTimeout(() => { toasts.items = toasts.items.filter((t) => t.id !== id); }, kind === 'err' ? 6000 : 3500);
}
export const fail = (e) => toast(e?.message || String(e), 'err');
