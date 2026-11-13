<script>
  import { session } from '../lib/session.svelte.js';
  import { route } from '../lib/router.svelte.js';
  import Logo from '../lib/Logo.svelte';
  import Icon from '../lib/Icon.svelte';

  const q = route.query;
  const fields = ['client_id', 'redirect_uri', 'state', 'code_challenge', 'code_challenge_method', 'response_type'];
  let device = $state(guess());
  function guess() {
    const n = q.get('client_name') || 'Claude Code';
    const os = /Mac/.test(navigator.platform) ? 'Mac' : /Win/.test(navigator.platform) ? 'Windows PC' : /Linux/.test(navigator.platform) ? 'Linux machine' : 'computer';
    return n === 'Claude Code' ? `Claude Code on ${session.user.display_name.split(' ')[0]}’s ${os}` : n;
  }
  const dest = (() => { try { return new URL(q.get('redirect_uri')); } catch { return null; } })();
  const loopback = dest && dest.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(dest.hostname);
  const port = loopback ? dest.port : '';
  const complete = ['client_id', 'redirect_uri', 'code_challenge'].every((f) => q.get(f));
  const home = `/users/${session.user.username}`;
</script>

<div class="wrap">
  {#if !complete}
  <div class="card box">
    <span class="tile"><Logo size={22} /></span>
    <h1 style="font-size:22px">This sign-in link is incomplete</h1>
    <p class="muted" style="margin:0;font-size:13px;line-height:1.55">Start again from Claude Code: run <span class="mono">/mcp</span>, choose <b style="color:var(--text);font-weight:500">artifact-buckets</b>, then <b style="color:var(--text);font-weight:500">Authenticate</b>.</p>
    <a class="btn" href="/connect" style="align-self:flex-start">Connect Claude Code</a>
  </div>
  {:else}
  <form class="card box" method="post" action="/oauth/approve">
    {#each fields as f}<input type="hidden" name={f} value={q.get(f) || ''} />{/each}
    <input type="hidden" name="csrf" value={session.csrf} />
    <div class="row" style="gap:14px">
      <span class="tile"><Logo size={22} /></span>
      <span class="dash"></span>
      <span class="tile cc"><Icon name="terminal" size={20} /></span>
    </div>
    <div style="display:flex;flex-direction:column;gap:8px">
      <h1 style="font-size:22px">Connect Claude Code to Artifact Buckets?</h1>
      <p class="muted" style="margin:0;font-size:13px;line-height:1.55">Signed in as <b style="color:var(--text);font-weight:500">{session.user.display_name}</b>. {#if loopback}Claude Code is waiting on this computer{port ? ` (port ${port})` : ''}.{/if}</p>
    </div>
    {#if !loopback}
      <div class="foreign"><Icon name="alert" /><span>This sign-in goes to <b class="mono">{dest?.host || 'an unknown address'}</b>, not to Claude Code on this computer. Connect only if you started it there yourself.</span></div>
    {/if}
    <ul class="can">
      <li><Icon name="check" /><span>Save files to <span class="mono">{home}</span> and to team folders you can edit</span></li>
      <li><Icon name="check" /><span>Find, open, move, rename and delete files you have access to</span></li>
      <li><Icon name="check" /><span>Share your folders, the same as you can on the web</span></li>
      {#if session.isAdmin}<li><Icon name="shield" style="color:var(--amber)" /><span>Use admin tools, because you are an admin</span></li>{/if}
    </ul>
    <label class="lbl">Name this computer<input class="field" name="device_name" bind:value={device} maxlength="128" /></label>
    <p class="note" style="margin:0">It shows under Connect Claude Code, where you can disconnect it at any time. The sign-in lasts 30 days of use.</p>
    <div class="row" style="justify-content:flex-end;gap:10px">
      <!-- Connect is the form's only submit button, so Enter in the name field connects. Cancel sends "deny". -->
      <input type="hidden" name="decision" value="allow" />
      <button type="button" class="btn" onclick={(e) => { const f = e.currentTarget.form; f.elements.decision.value = 'deny'; f.requestSubmit(); }}>Cancel</button>
      <button type="submit" class="btn pri">Connect</button>
    </div>
  </form>
  {/if}
</div>

<style>
  .wrap { min-height: 100vh; display: grid; place-items: center; padding: 32px 16px; background: radial-gradient(900px 500px at 50% 0%, #17130C 0%, transparent 70%), var(--bg); }
  .box { width: 100%; max-width: 480px; padding: 30px; display: flex; flex-direction: column; gap: 22px; }
  .tile { width: 46px; height: 46px; border-radius: 12px; background: #1A1B1F; border: 1px solid var(--line-strong); display: grid; place-items: center; }
  .tile.cc { color: #E8A27A; }
  .dash { flex: 0 0 40px; border-top: 1.5px dashed var(--line-hover); }
  .can { list-style: none; margin: 0; padding: 14px 16px; border-radius: 10px; background: var(--card-2); border: 1px solid var(--line); display: flex; flex-direction: column; gap: 10px; font-size: 13px; line-height: 1.45; }
  .can li { display: flex; gap: 10px; align-items: flex-start; }
  .foreign { display: flex; gap: 10px; align-items: flex-start; padding: 12px 14px; border-radius: 10px; background: var(--red-bg); border: 1px solid #5A2E24; color: var(--text-2); font-size: 13px; line-height: 1.5; }
  .foreign :global(.ic) { color: var(--red); flex: none; margin-top: 1px; }
  .can :global(.ic) { color: var(--green); margin-top: 1px; }
</style>
