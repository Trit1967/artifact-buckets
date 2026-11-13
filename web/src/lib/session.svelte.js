// Who is signed in, plus server facts the pages need (SMB host, Entra on/off...).
import { navigate } from './router.svelte.js';

export const session = $state({
  loaded: false, user: null, csrf: null, oidc: false, smb: false, smb_host: '', base_url: '', marketplace: '',
  get isAdmin() { return this.user?.system_role === 'admin'; },
  get isAuditor() { return ['admin', 'auditor'].includes(this.user?.system_role); },
  async load() {
    const r = await fetch('/api/auth/me', { credentials: 'same-origin' });
    const d = await r.json();
    Object.assign(this, { ...d, user: d.user, csrf: d.csrf || null, loaded: true });
  },
  set(data) { this.user = data.user; this.csrf = data.csrf; },
  expired() {
    if (!this.user) return;
    this.user = null; this.csrf = null;
    const here = location.pathname + location.search;
    navigate(`/login?next=${encodeURIComponent(here)}`, true);
  },
});
