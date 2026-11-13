<script>
  import { api } from '../lib/api.js';
  import { session } from '../lib/session.svelte.js';
  import { toast, fail } from '../lib/toast.svelte.js';
  import Icon from '../lib/Icon.svelte';
  import Copy from '../lib/Copy.svelte';

  let d = $state(null);
  let busy = $state(false);
  async function load() { try { d = await api.get('/api/smb'); } catch (e) { fail(e); } }
  load();
  async function reconcile() {
    busy = true;
    try { const r = await api.post('/api/smb/reconcile'); toast(`Permissions written to ${r.items} files and folders`); d = { ...d, drift: r.drift }; } catch (e) { fail(e); } finally { busy = false; }
  }
  const SEC = [
    ['SMB 3.1.1 only', 'Older protocol versions are refused.'],
    ['Encryption required', 'Every session is encrypted, inside and outside the LAN.'],
    ['Signing required', 'Packets can’t be tampered with.'],
    ['Kerberos only', 'NTLM and guest access are off. Domain sign-in, no passwords stored here.'],
    ['Access-based enumeration', 'People only see folders they can open.'],
    ['Permissions from the app', 'Changing permissions from Explorer is off. Grants made in the app are the single source.'],
    ['Every file operation recorded', 'Samba records opens, writes, renames and deletes in its own log for your SIEM. Files added over the share appear here within a minute.'],
  ];
</script>

<div class="page">
  <header class="page-head">
    <div><h1>SMB shares</h1><p class="sub">The same folders, mapped as drives in Windows. Permissions come from the grants in this app and are written to the files, so Explorer shows them as normal NTFS permissions.</p></div>
    {#if session.isAdmin && d?.acl_tools}<button class="btn" onclick={reconcile} disabled={busy}><Icon name="refresh" />{busy ? 'Writing…' : 'Re-apply permissions'}</button>{/if}
  </header>

  {#if !d}<div class="card empty">Loading…</div>{:else}
  {#if !d.enabled}
    <div class="banner"><Icon name="info" /><span>The share is off in this environment. In a domain deployment it runs as its own container joined to the domain; set <span class="mono">AB_SMB_ENABLED=true</span> on the app so it writes permissions.</span></div>
  {/if}
  <section class="shares">
    {#each d.shares as s}
      <div class="card sh">
        <div class="row" style="gap:12px"><span class="drive mono">{s.drive}</span><span style="display:flex;flex-direction:column;gap:3px"><span style="font-weight:600">{s.name}</span><span class="note mono">{s.path}</span></span></div>
        <Copy text={s.unc} />
        <span class="note">{s.name === 'home' ? 'Each person lands in their own folder.' : 'Team folders; each person sees only the ones they can open.'} Mapped by Group Policy, or by hand with <span class="mono">net use {s.drive} {s.unc}</span>.</span>
      </div>
    {/each}
  </section>

  <section class="duo">
    <div class="card pad">
      <h2>Security</h2>
      <ul class="sec-list">
        {#each SEC as [t, n]}<li><Icon name="check" /><span><b>{t}.</b> {n}</span></li>{/each}
      </ul>
    </div>
    <div class="card pad">
      <h2>How roles appear in Windows</h2>
      <table class="table">
        <thead><tr><th>In the app</th><th>In Explorer</th></tr></thead>
        <tbody>
          <tr><td><span class="tag blue">Viewer</span></td><td>Read and execute, list folder contents</td></tr>
          <tr><td><span class="tag green">Editor</span></td><td>Modify</td></tr>
          <tr><td><span class="tag amber">Owner</span></td><td>Modify (sharing is done in the app)</td></tr>
        </tbody>
      </table>
      <p class="note" style="margin:0">Only AD people and groups appear on the share. Local accounts use the browser and Claude Code. {d.folders_with_grants} folders carry their own sharing.</p>
    </div>
  </section>

  <section class="card">
    <div class="hd"><div style="display:flex;flex-direction:column;gap:4px"><h2>Permission check</h2><span class="note">Compares what's on disk with the grants. Anything changed on the server by hand shows here.</span></div>
      {#if d.drift.length}<span class="tag red">{d.drift.length} out of step</span>{:else if d.acl_tools && d.enabled}<span class="tag green">All match</span>{/if}</div>
    {#if !d.enabled || !d.acl_tools}<div class="empty">Runs when the share is on.</div>
    {:else if !d.drift.length}<div class="empty"><Icon name="check" style="color:var(--green);display:inline-block;vertical-align:-3px" /> Every folder matches its grants.</div>
    {:else}
      <div class="scroll-x"><table class="table">
        <thead><tr><th>Folder</th><th>Expected</th><th>On disk</th></tr></thead>
        <tbody>
          {#each d.drift as x}
            <tr><td class="mono">{x.path}</td><td class="mono note">{Object.entries(x.expected).map(([k, v]) => `${k} ${v}`).join(', ') || '—'}</td><td class="mono" style="color:var(--red);font-size:12px">{Object.entries(x.found).map(([k, v]) => `${k} ${v}`).join(', ') || '—'}</td></tr>
          {/each}
        </tbody>
      </table></div>
    {/if}
  </section>

  <section class="card">
    <div class="hd"><h2>Open sessions</h2><span class="note">{d.sessions.length}</span></div>
    {#if !d.sessions.length}<div class="empty">Nobody is connected to the share right now.</div>
    {:else}
      <div class="scroll-x"><table class="table">
        <thead><tr><th>Person</th><th>Computer</th><th>Protocol</th><th>Encrypted</th><th>Open files</th></tr></thead>
        <tbody>
          {#each d.sessions as s}
            <tr><td>{s.user}</td><td class="mono note">{s.machine}</td><td class="mono">{s.protocol}</td><td>{#if s.encrypted}<span class="tag green">Yes</span>{:else}<span class="tag red">No</span>{/if}</td><td class="mono">{s.open_files ?? 0}</td></tr>
          {/each}
        </tbody>
      </table></div>
    {/if}
  </section>
  {/if}
</div>

<style>
  .banner { display: flex; gap: 12px; align-items: flex-start; padding: 14px 18px; border-radius: 12px; background: #13161F; border: 1px solid #2A3150; font-size: 13px; line-height: 1.55; }
  .banner :global(.ic) { color: var(--blue); margin-top: 2px; }
  .shares { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }
  .sh { padding: 20px 22px; display: flex; flex-direction: column; gap: 14px; }
  .drive { width: 40px; height: 40px; border-radius: 10px; background: var(--amber-bg); color: var(--amber); display: grid; place-items: center; font-weight: 500; }
  .duo { display: flex; flex-wrap: wrap; gap: 16px; }
  .duo > * { flex: 1 1 380px; }
  .pad { padding: 22px 24px; display: flex; flex-direction: column; gap: 14px; }
  .sec-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; font-size: 13px; line-height: 1.5; color: var(--text-2); }
  .sec-list li { display: flex; gap: 10px; align-items: flex-start; }
  .sec-list :global(.ic) { color: var(--green); margin-top: 2px; }
  .sec-list b { color: var(--text); font-weight: 500; }
  .hd { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 18px 22px; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
</style>
