<script>
  import { route, navigate } from './lib/router.svelte.js';
  import { session } from './lib/session.svelte.js';
  import { api } from './lib/api.js';
  import { bytes } from './lib/format.js';
  import Icon from './lib/Icon.svelte';
  import Logo from './lib/Logo.svelte';
  import Avatar from './lib/Avatar.svelte';
  import Toasts from './lib/Toasts.svelte';
  import ChangePassword from './lib/ChangePassword.svelte';
  import Login from './pages/Login.svelte';
  import Consent from './pages/Consent.svelte';
  import Files from './pages/Files.svelte';
  import Viewer from './pages/Viewer.svelte';
  import Trash from './pages/Trash.svelte';
  import Incoming from './pages/Incoming.svelte';
  import Connect from './pages/Connect.svelte';
  import Overview from './pages/Overview.svelte';
  import Users from './pages/Users.svelte';
  import Groups from './pages/Groups.svelte';
  import Storage from './pages/Storage.svelte';
  import Directory from './pages/Directory.svelte';
  import Smb from './pages/Smb.svelte';
  import Audit from './pages/Audit.svelte';
  import Link from './pages/Link.svelte';
  import Welcome from './pages/Welcome.svelte';

  const PUBLIC = ['/login'];
  const PAGES = {
    '/files': [Files, 'all'], '/view': [Viewer, 'all'], '/trash': [Trash, 'all'], '/incoming': [Incoming, 'all'], '/connect': [Connect, 'all'],
    '/overview': [Overview, 'auditor'], '/users': [Users, 'auditor'], '/groups': [Groups, 'all'], '/storage': [Storage, 'auditor'],
    '/directory': [Directory, 'auditor'], '/smb': [Smb, 'auditor'], '/audit': [Audit, 'auditor'], '/consent': [Consent, 'all'],
    '/link': [Link, 'all'], '/welcome': [Welcome, 'all'],
  };
  const FULL = ['/login', '/consent', '/link', '/welcome'];

  let pool = $state(null);
  session.load();

  $effect(() => {
    if (!session.loaded) return;
    const p = route.path;
    if (!session.user && !PUBLIC.includes(p)) {
      navigate(`/login?next=${encodeURIComponent(p + (route.query.toString() ? '?' + route.query : ''))}`, true);
    } else if (session.user && (p === '/' || p === '/login')) {
      const home = session.welcome ? '/welcome' : session.isAuditor ? '/overview' : '/files';
      navigate(route.query.get('next') && p === '/login' ? safeNext(route.query.get('next')) : home, true);
    }
  });

  $effect(() => {
    if (session.user && session.isAuditor) api.get('/api/storage', null, { quiet: true }).then((d) => (pool = d)).catch(() => {});
  });

  function safeNext(n) {
    try { const u = new URL(n, location.origin); return u.origin === location.origin ? u.pathname + u.search : '/'; } catch { return '/'; }
  }

  // /oauth/... targets are server routes: leave the SPA for them.
  $effect(() => {
    if (session.user && route.path === '/login') {
      const n = route.query.get('next');
      if (n && n.startsWith('/oauth/')) location.assign(n);
    }
  });

  const page = $derived(PAGES[route.path]);
  const Page = $derived(page?.[0]);
  const allowed = $derived(page && (page[1] === 'all' || session.isAuditor));
  const full = $derived(FULL.includes(route.path));

  async function signOut() {
    await api.post('/api/auth/logout').catch(() => {});
    session.user = null; session.csrf = null;
    navigate('/login', true);
  }

  const nav = $derived([
    { sec: 'Workspace' },
    { href: '/files', label: 'Files', icon: 'folder' },
    { href: '/incoming', label: 'Claude Code saves', icon: 'ingest' },
    { href: '/connect', label: 'Connect Claude Code', icon: 'link' },
    { href: '/groups', label: session.isAuditor ? 'Groups and access' : 'My groups', icon: 'shield', show: !session.isAuditor },
    ...(session.isAuditor ? [
      { sec: 'Administration' },
      { href: '/overview', label: 'Overview', icon: 'grid' },
      { href: '/users', label: 'Users', icon: 'users' },
      { href: '/groups', label: 'Groups and access', icon: 'shield' },
      { href: '/storage', label: 'Storage', icon: 'db' },
      { href: '/directory', label: 'Directory', icon: 'building' },
      { href: '/smb', label: 'SMB shares', icon: 'server', show: session.smb },
      { href: '/audit', label: 'Audit log', icon: 'list' },
    ] : []),
  ].filter((n) => n.show !== false));

  let menu = $state(false);
  let pwOpen = $state(false);
  let drawer = $state(false);
  $effect(() => { route.path; drawer = false; menu = false; });
  const used = $derived(pool ? pool.used_bytes : 0);
  const total = $derived(pool ? pool.total_bytes : 0);
</script>

<Toasts />
{#if pwOpen && session.user}<ChangePassword onclose={() => (pwOpen = false)} />{/if}

{#if !session.loaded}
  <div class="boot"><Logo size={28} /></div>
{:else if full || !session.user}
  {#if route.path === '/login'}<Login />
  {:else if session.user && route.path === '/consent'}<Consent />
  {:else if session.user && route.path === '/link'}<Link />
  {:else if session.user && route.path === '/welcome'}<Welcome />{/if}
{:else}
  <div class="shell">
    <aside class="side">
      <div class="toprow">
        <a href={session.isAuditor ? '/overview' : '/files'} class="brand"><Logo /><span>Artifact Buckets</span></a>
        <button class="btn icon burger" aria-label="Menu" aria-expanded={drawer} onclick={() => (drawer = !drawer)}><Icon name={drawer ? 'x' : 'list'} /></button>
      </div>
      <nav aria-label="Main" class="nav-list" class:open={drawer}>
        {#each nav as n, i (n.sec ? 's' + i : n.href + i)}
          {#if n.sec}
            <span class="sec" style:padding-top={i ? '20px' : '0'}>{n.sec}</span>
          {:else}
            <a class="nav" class:on={route.path === n.href} aria-current={route.path === n.href ? 'page' : undefined} href={n.href}>
              <Icon name={n.icon} />{n.label}
            </a>
          {/if}
        {/each}
      </nav>
      <div class="foot">
        {#if session.isAuditor && pool && total}
          <a class="meter-card" href="/storage">
            <span class="mc-label">{pool.mode === 'direct' ? 'Disk space' : 'Storage pool'}</span>
            <span class="mc-val"><b>{bytes(used, 1)}</b> of {bytes(total, 0)}</span>
            <div class="meter"><div style="width:{Math.max(1.5, Math.min(100, (used / total) * 100))}%"></div></div>
          </a>
        {:else if !session.isAuditor && session.user.quota_bytes}
          <a class="meter-card" href={`/files?path=${encodeURIComponent(session.user.home)}`}>
            <span class="mc-label">Your space</span>
            <span class="mc-val"><b>{bytes(session.user.used_bytes, 1)}</b> of {bytes(session.user.quota_bytes, 0)}</span>
            <div class="meter"><div style="width:{Math.max(1.5, Math.min(100, (session.user.used_bytes / session.user.quota_bytes) * 100))}%"></div></div>
          </a>
        {/if}
        <div class="me">
          <button class="mebtn" onclick={() => (menu = !menu)} aria-expanded={menu} aria-label="Account">
            <Avatar name={session.user.display_name} size={32} />
            <span class="grow who">
              <span class="who-n">{session.user.display_name}</span>
              <span class="who-r">{({ admin: 'Administrator', auditor: 'Auditor', member: 'Member' })[session.user.system_role]}</span>
            </span>
            <Icon name="more" />
          </button>
          {#if menu}
            <div class="menu card">
              <div class="note" style="padding:8px 10px">Signed in as <span class="mono">{session.user.username}</span></div>
              <a class="mi" href="/welcome"><Icon name="list" />Getting started</a>
              <button class="mi" onclick={() => { menu = false; pwOpen = true; }}><Icon name="key" />Change password</button>
              <button class="mi" onclick={signOut}><Icon name="signout" />Sign out</button>
            </div>
          {/if}
        </div>
      </div>
    </aside>
    <main class="main">
      {#if page && allowed}
        {#key route.path}<Page />{/key}
      {:else if page}
        <div class="page"><div class="card empty">Only admins and auditors can open this page.</div></div>
      {:else}
        <div class="page"><div class="card empty">There's no page here. <a href="/files">Go to your files</a>.</div></div>
      {/if}
    </main>
  </div>
{/if}

<style>
  .boot { min-height: 100vh; display: grid; place-items: center; opacity: .6; }
  .shell { display: flex; min-height: 100vh; }
  .side { flex: 0 0 248px; padding: 20px 14px; border-right: 1px solid var(--line-soft); background: var(--bg-side); display: flex; flex-direction: column; gap: 28px;
    position: sticky; top: 0; height: 100vh; overflow-y: auto; }
  .brand { display: flex; align-items: center; gap: 10px; padding: 6px 10px; color: var(--text); font-size: 15px; font-weight: 600; letter-spacing: -0.01em; }
  .brand:hover { color: var(--text); }
  .nav-list { display: flex; flex-direction: column; gap: 2px; }
  .nav-list .sec { padding-left: 12px; padding-bottom: 6px; }
  .nav { display: flex; align-items: center; gap: 12px; height: 38px; padding: 0 12px; border-radius: 8px; color: var(--muted); font-size: 14px; font-weight: 500; }
  .nav:hover { background: #16181B; color: var(--text); }
  .nav.on { background: var(--raise); color: var(--text); box-shadow: inset 0 0 0 1px #292C32; }
  .nav.on :global(.ic) { color: var(--amber); }
  .toprow { display: flex; align-items: center; justify-content: space-between; }
  .burger { display: none; }
  .foot { margin-top: auto; display: flex; flex-direction: column; gap: 12px; }
  .meter-card { display: flex; flex-direction: column; gap: 6px; padding: 14px; border-radius: 12px; background: #121316; border: 1px solid #1E2024; color: var(--text); }
  .meter-card:hover { border-color: var(--line-hover); color: var(--text); }
  .meter-card .meter { margin-top: 4px; }
  .mc-label { font-size: 11px; letter-spacing: .08em; text-transform: uppercase; color: var(--faint); font-weight: 500; }
  .mc-val { font-size: 12.5px; color: var(--muted); white-space: nowrap; font-variant-numeric: tabular-nums; }
  .mc-val b { color: var(--text); font-weight: 600; font-size: 14px; }
  .me { position: relative; }
  .mebtn { all: unset; box-sizing: border-box; cursor: pointer; display: flex; align-items: center; gap: 10px; padding: 6px; border-radius: 10px; width: 100%; color: var(--muted); }
  .mebtn:hover { background: #16181B; }
  .mebtn:focus-visible, .mi:focus-visible { outline: 2px solid var(--amber); outline-offset: 1px; }
  .who { display: flex; flex-direction: column; gap: 2px; text-align: left; min-width: 0; }
  .who-n { font-size: 13px; font-weight: 500; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .who-r { font-size: 12px; color: var(--muted); }
  .menu { position: absolute; bottom: 52px; left: 0; right: 0; padding: 6px; box-shadow: 0 16px 40px rgba(0, 0, 0, .5); z-index: 5; }
  .mi { all: unset; box-sizing: border-box; cursor: pointer; display: flex; gap: 10px; align-items: center; width: 100%; padding: 9px 10px; border-radius: 7px; font-size: 13px; }
  .mi:hover { background: var(--raise); }
  a.mi { color: var(--text); }
  .main { flex: 1; min-width: 0; }
  @media (max-width: 900px) {
    .shell { flex-direction: column; }
    .side { position: sticky; top: 0; z-index: 30; height: auto; flex: none; border-right: 0; border-bottom: 1px solid var(--line-soft); gap: 0; padding: 10px 14px; overflow: visible; }
    .burger { display: inline-flex; }
    .nav-list, .foot { display: none; }
    .nav-list.open { display: flex; padding-top: 12px; }
    .nav-list.open ~ .foot { display: flex; padding-top: 14px; margin-top: 0; }
    .menu { bottom: auto; top: 52px; }
  }
</style>
