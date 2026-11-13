// Path-based routing with the History API. The server answers every page path with index.html.
export const route = $state({ path: location.pathname, query: new URLSearchParams(location.search) });

function sync() {
  route.path = location.pathname;
  route.query = new URLSearchParams(location.search);
}

export function navigate(to, replace = false) {
  if (to === location.pathname + location.search) return;
  history[replace ? 'replaceState' : 'pushState']({}, '', to);
  sync();
  window.scrollTo(0, 0);
}

window.addEventListener('popstate', sync);

// Plain <a href="/..."> links inside the app navigate without a reload.
document.addEventListener('click', (e) => {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const a = e.target.closest('a[href]');
  if (!a || a.target || a.hasAttribute('download') || a.dataset.native !== undefined) return;
  const url = new URL(a.href, location.href);
  if (url.origin !== location.origin) return;
  if (/^\/(api|oauth|auth|preview|mcp)\b/.test(url.pathname)) return;
  e.preventDefault();
  navigate(url.pathname + url.search);
});
