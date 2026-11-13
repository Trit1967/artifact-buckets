// Small client for the app's REST API. The session cookie travels automatically;
// every write also sends the CSRF value the server gave us at sign-in.
import { session } from './session.svelte.js';

export class ApiError extends Error {
  constructor(status, body) {
    super(body?.message || body?.error || `Request failed (${status})`);
    this.status = status;
    this.code = body?.error;
    this.body = body;
  }
}

async function request(method, url, body, opts = {}) {
  const headers = { Accept: 'application/json' };
  if (method !== 'GET' && session.csrf) headers['X-CSRF-Token'] = session.csrf;
  let payload;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
  const res = await fetch(url, { method, headers, body: payload, credentials: 'same-origin', signal: opts.signal });
  const type = res.headers.get('content-type') || '';
  const data = type.includes('application/json') ? await res.json() : await res.text();
  if (!res.ok) {
    if (res.status === 401 && !opts.quiet) session.expired();
    throw new ApiError(res.status, typeof data === 'object' ? data : { message: data });
  }
  return data;
}

const qs = (params) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) if (v !== undefined && v !== null && v !== '') p.set(k, v);
  const s = p.toString();
  return s ? `?${s}` : '';
};

export const api = {
  get: (url, params, opts) => request('GET', url + qs(params), undefined, opts),
  post: (url, body, opts) => request('POST', url, body ?? {}, opts),
  put: (url, body) => request('PUT', url, body ?? {}),
  patch: (url, body) => request('PATCH', url, body ?? {}),
  del: (url) => request('DELETE', url),
  download: (path) => `/api/fs/download${qs({ path })}`,
};
