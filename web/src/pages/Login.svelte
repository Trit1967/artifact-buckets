<script>
  import { api } from '../lib/api.js';
  import { session } from '../lib/session.svelte.js';
  import { route, navigate } from '../lib/router.svelte.js';
  import Logo from '../lib/Logo.svelte';
  import Icon from '../lib/Icon.svelte';

  let username = $state('');
  let password = $state('');
  let busy = $state(false);
  let error = $state(route.query.get('error') === 'not_allowed' ? 'Your Microsoft account is not set up for Artifact Buckets. Ask an admin to add you to an AB- group.' : '');
  const next = $derived(route.query.get('next') || '');
  const fromClaude = $derived(next.startsWith('/oauth/authorize'));

  function go() {
    const n = next && next.startsWith('/') && !next.startsWith('//') ? next : '/';
    if (n.startsWith('/oauth/')) location.assign(n);
    else navigate(n, true);
  }

  async function submit(e) {
    e.preventDefault();
    busy = true; error = '';
    try {
      const d = await api.post('/api/auth/login', { username, password }, { quiet: true });
      session.set(d);
      go();
    } catch (err) {
      error = err.status === 401 ? 'That username and password don’t match an active account.' : err.message;
    } finally { busy = false; }
  }
</script>

<div class="wrap">
  <section class="hero" aria-hidden="true">
    <div class="stack">
      {#each [['latency-dashboard.html', '/shared/platform-eng/dashboards', '#F2A93B', 'html'], ['q3-forecast.md', '/users/maya/projects/finance', '#8AA4FF', 'md'], ['schema-diff.json', '/shared/data/migrations', '#4CC38A', 'json'], ['brand-tokens.svg', '/shared/design/system', '#D59BE6', 'svg']] as [n, p, c, e], i}
        <div class="slab" style="--i:{i}">
          <span class="ext mono" style="color:{c};background:{c}1A">{e}</span>
          <span class="grow"><span class="nm">{n}</span><span class="pt mono">{p}</span></span>
          <span class="src"><span class="dot" style="background:{c}"></span>Claude Code</span>
        </div>
      {/each}
    </div>
    <div class="claim">
      <h2>Every artifact Claude Code makes, kept where your team can find it.</h2>
      <p>Saved straight from Claude Code. Organized by person and team. Opened in the browser or from Windows.</p>
    </div>
  </section>

  <section class="formside">
    <form class="form" onsubmit={submit}>
      <div class="row" style="gap:12px"><Logo size={30} /><span style="font-size:17px;font-weight:600;letter-spacing:-0.01em">Artifact Buckets</span></div>
      <div style="display:flex;flex-direction:column;gap:6px">
        <h1>{fromClaude ? 'Sign in to connect Claude Code' : 'Sign in'}</h1>
        <p class="muted" style="margin:0;font-size:13px;line-height:1.5">
          {fromClaude ? 'After you sign in you’ll confirm which computer is connecting.' : 'Use your work account.'}
        </p>
      </div>
      {#if session.oidc}
        <a class="btn ms" data-native href={`/auth/oidc/login?next=${encodeURIComponent(next || '/')}`}>
          <svg width="16" height="16" viewBox="0 0 21 21" aria-hidden="true"><rect x="1" y="1" width="9" height="9" fill="#F25022"/><rect x="11" y="1" width="9" height="9" fill="#7FBA00"/><rect x="1" y="11" width="9" height="9" fill="#00A4EF"/><rect x="11" y="11" width="9" height="9" fill="#FFB900"/></svg>
          Continue with Microsoft
        </a>
        <div class="or"><span>or with a local account</span></div>
      {/if}
      <label class="lbl">Username<input class="field" autocomplete="username" bind:value={username} required autocapitalize="none" spellcheck="false" /></label>
      <label class="lbl">Password<input class="field" type="password" autocomplete="current-password" bind:value={password} required /></label>
      {#if error}<div class="err row" style="align-items:flex-start"><Icon name="alert" style="margin-top:1px" />{error}</div>{/if}
      <button class="btn pri" style="height:40px" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
    </form>
  </section>
</div>

<style>
  .wrap { min-height: 100vh; display: flex; flex-wrap: wrap; }
  .hero { flex: 1 1 520px; background: radial-gradient(1200px 600px at 20% 10%, #19140B 0%, transparent 60%), var(--bg-side); border-right: 1px solid var(--line-soft);
    padding: 64px 56px; display: flex; flex-direction: column; justify-content: center; gap: 64px; overflow: hidden; }
  .stack { display: flex; flex-direction: column; gap: 12px; max-width: 520px; }
  .slab { display: flex; align-items: center; gap: 14px; padding: 14px 16px; border-radius: 12px; background: var(--card); border: 1px solid var(--line);
    transform: translateX(calc(var(--i) * 26px)); opacity: calc(1 - var(--i) * .16); animation: slide .5s calc(var(--i) * 80ms) ease-out both; }
  .ext { width: 44px; height: 30px; border-radius: 7px; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 500; letter-spacing: .05em; text-transform: uppercase; }
  .grow { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
  .nm { font-weight: 500; font-size: 13px; }
  .pt { font-size: 11px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .src { display: flex; align-items: center; gap: 7px; font-size: 12px; color: var(--muted); white-space: nowrap; }
  .claim h2 { font-size: 30px; line-height: 1.15; letter-spacing: -0.03em; font-weight: 600; max-width: 520px; }
  .claim p { color: var(--muted); max-width: 460px; line-height: 1.6; margin: 14px 0 0; }
  .formside { flex: 1 1 420px; display: flex; align-items: center; justify-content: center; padding: 48px 24px; }
  .form { width: 100%; max-width: 360px; display: flex; flex-direction: column; gap: 18px; }
  .ms { height: 40px; background: #F4F5F6; color: #16181B; border-color: #F4F5F6; }
  .ms:hover { background: #fff; color: #16181B; }
  .or { display: flex; align-items: center; gap: 12px; font-size: 12px; color: var(--faint); }
  .or::before, .or::after { content: ''; height: 1px; background: var(--line); flex: 1; }
  @keyframes slide { from { opacity: 0; transform: translateX(calc(var(--i) * 26px - 16px)); } }
  @media (max-width: 900px) { .hero { display: none; } }
</style>
