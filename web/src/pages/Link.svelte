<script>
  // "Let this computer sync?" The sync script on a computer shows a code and opens this page; the person
  // compares the code and presses Allow. Like signing in to a TV app.
  import { api } from '../lib/api.js';
  import { session } from '../lib/session.svelte.js';
  import { route } from '../lib/router.svelte.js';
  import Logo from '../lib/Logo.svelte';
  import Icon from '../lib/Icon.svelte';

  const code = (route.query.get('code') || '').trim().toUpperCase();
  let info = $state(null);
  let error = $state(code ? '' : 'missing');
  let busy = $state(false);
  let outcome = $state(null);   // 'approved' | 'denied'
  const home = `/users/${session.user.username}`;

  if (code) {
    api.get(`/api/sync/link/${encodeURIComponent(code)}`, null, { quiet: true })
      .then((d) => { info = d; if (d.status !== 'pending') error = 'used'; })
      .catch((e) => { error = e.status === 404 ? 'expired' : 'other'; });
  }

  async function decide(d) {
    busy = true;
    try { outcome = (await api.post(`/api/sync/link/${encodeURIComponent(code)}/${d}`)).status; }
    catch (e) { error = e.status === 404 ? 'expired' : e.status === 400 ? 'used' : 'other'; }
    finally { busy = false; }
  }
  const chars = $derived((info?.code || code).split(''));
</script>

<div class="wrap">
  <div class="card box">
    {#if error}
      <span class="tile"><Logo size={22} /></span>
      <div class="txt">
        <h1>{error === 'expired' ? 'This code has expired' : error === 'used' ? 'This code was already used' : error === 'missing' ? 'There’s no code in this link' : 'Something went wrong'}</h1>
        <p class="muted">Codes last ten minutes and work once. On the computer, run the link step again and open the new link it shows.</p>
      </div>
      <a class="btn" href="/connect" style="align-self:flex-start">Connect Claude Code</a>
    {:else if !info}
      <span class="tile"><Logo size={22} /></span>
      <p class="muted" style="margin:0">Checking the code…</p>
    {:else if outcome === 'approved'}
      <div class="row" style="gap:14px">
        <span class="tile"><Logo size={22} /></span>
        <span class="dash done"></span>
        <span class="tile pc ok"><Icon name="check" size={20} /></span>
      </div>
      <div class="txt">
        <h1><span class="name">{info.computer}</span> is linked</h1>
        <p class="muted">You can close this tab. The computer is copying the files Claude made into <span class="mono">{home}/projects</span> and <span class="mono">{home}/inbox</span>, and from now on it adds new ones after each Claude Code reply.</p>
      </div>
      <div class="row" style="gap:10px">
        <a class="btn pri" href={session.welcome ? '/welcome?step=work' : '/connect#sync'}>Watch them arrive</a>
        <a class="btn" href={`/files?path=${encodeURIComponent(home + '/projects')}`}>Open my projects</a>
      </div>
    {:else if outcome === 'denied'}
      <span class="tile"><Logo size={22} /></span>
      <div class="txt">
        <h1>Not linked</h1>
        <p class="muted"><span class="name">{info.computer}</span> can’t sync, and nothing was copied. You can close this tab.</p>
      </div>
    {:else}
      <div class="row" style="gap:14px">
        <span class="tile"><Logo size={22} /></span>
        <span class="dash"></span>
        <span class="tile pc"><Icon name="laptop" size={20} /></span>
      </div>
      <div class="txt">
        <h1>Let <span class="name">{info.computer}</span> sync to your folders?</h1>
        <p class="muted">Signed in as <b>{session.user.display_name}</b>. Check that the computer shows the same code.</p>
      </div>
      <div class="code mono" aria-label={`Code ${info.code}`}>
        {#each chars as ch}{#if ch === '-'}<span class="sep"></span>{:else}<span class="ch">{ch}</span>{/if}{/each}
      </div>
      <ul class="can">
        <li><Icon name="check" /><span>Copy the files Claude Code makes on this computer into <span class="mono">{home}/projects</span>, and loose ones into <span class="mono">inbox</span></span></li>
        <li><Icon name="check" /><span>Leave out files kept in git, secrets like <span class="mono">.env</span> and keys, and build folders</span></li>
        <li class="no"><Icon name="x" /><span>It can’t open, change, delete or share anything already here</span></li>
      </ul>
      <p class="note" style="margin:0">It shows under Connect Claude Code, where you can turn it off at any time.</p>
      <div class="row" style="justify-content:flex-end;gap:10px">
        <button class="btn" disabled={busy} onclick={() => decide('deny')}>Don’t allow</button>
        <button class="btn pri" disabled={busy} onclick={() => decide('allow')}>Allow</button>
      </div>
    {/if}
  </div>
</div>

<style>
  .wrap { min-height: 100vh; display: grid; place-items: center; padding: 32px 16px; background: radial-gradient(900px 500px at 50% 0%, #17130C 0%, transparent 70%), var(--bg); }
  .box { width: 100%; max-width: 480px; padding: 30px; display: flex; flex-direction: column; gap: 22px; }
  .txt { display: flex; flex-direction: column; gap: 8px; }
  .txt h1 { font-size: 22px; margin: 0; line-height: 1.3; }
  .txt p { margin: 0; font-size: 13px; line-height: 1.55; }
  .txt b { color: var(--text); font-weight: 500; }
  .name { color: var(--text); overflow-wrap: anywhere; }
  .tile { width: 46px; height: 46px; border-radius: 12px; background: #1A1B1F; border: 1px solid var(--line-strong); display: grid; place-items: center; flex: none; }
  .tile.pc { color: var(--text-2); }
  .tile.ok { color: var(--green); border-color: #2C4A3A; background: var(--green-bg); }
  .dash { flex: 0 0 40px; border-top: 1.5px dashed var(--line-hover); }
  .dash.done { border-top-style: solid; border-color: #2C4A3A; }
  .code { display: flex; align-items: center; gap: 6px; padding: 16px; border-radius: 12px; background: #0F1012; border: 1px solid var(--line); justify-content: center; }
  .ch { width: 36px; height: 46px; border-radius: 8px; background: var(--raise); border: 1px solid var(--line-strong); display: grid; place-items: center; font-size: 22px; font-weight: 500; color: var(--amber-hi); }
  .sep { width: 10px; height: 2px; border-radius: 2px; background: var(--line-hover); margin: 0 4px; }
  .can { list-style: none; margin: 0; padding: 14px 16px; border-radius: 10px; background: var(--card-2); border: 1px solid var(--line); display: flex; flex-direction: column; gap: 10px; font-size: 13px; line-height: 1.45; }
  .can li { display: flex; gap: 10px; align-items: flex-start; }
  .can :global(.ic) { color: var(--green); margin-top: 1px; flex: none; }
  .can .no :global(.ic) { color: var(--faint); }
  .can .mono { font-size: 12px; overflow-wrap: anywhere; }
  @media (max-width: 420px) {
    .box { padding: 22px; }
    .code { gap: 4px; padding: 12px 8px; }
    .ch { width: 30px; height: 40px; font-size: 18px; }
    .sep { margin: 0 2px; }
  }
</style>
