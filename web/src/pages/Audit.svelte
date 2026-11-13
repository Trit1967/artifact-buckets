<script>
  import { api } from '../lib/api.js';
  import { fail } from '../lib/toast.svelte.js';
  import { when } from '../lib/format.js';
  import { describe, VIA } from '../lib/activity.js';
  import Icon from '../lib/Icon.svelte';

  let f = $state({ action: '', user: '', path: '', via: '', limit: 200 });
  let events = $state(null);
  let open = $state(null);
  async function load(e) {
    e?.preventDefault();
    try { events = (await api.get('/api/audit', f)).events; } catch (err) { fail(err); }
  }
  load();
  const exportUrl = $derived('/api/audit/export.csv?' + new URLSearchParams(Object.entries({ action: f.action, user: f.user, path: f.path, via: f.via }).filter(([, v]) => v)));
</script>

<div class="page">
  <header class="page-head">
    <div><h1>Audit log</h1><p class="sub">Every sign-in, save, open, move, delete, share and admin change, with who did it, from where and through which door. Entries can't be edited.</p></div>
    {#if events?.length}<a class="btn" data-native href={exportUrl} download title="Every matching entry, not only the ones shown"><Icon name="download" />Export CSV</a>{/if}
  </header>

  <form class="card filters" onsubmit={load}>
    <label class="lbl">Action<select class="field" bind:value={f.action}>
      <option value="">Any</option><option value="file.*">Files</option><option value="file.save">Saves</option><option value="item.*">Moves and deletes</option>
      <option value="grant.*">Sharing</option><option value="signin.*">Sign-ins</option><option value="oauth.*">Claude Code connects</option><option value="token.*">Tokens</option>
      <option value="user.*">People</option><option value="group.*">Groups</option><option value="denied">Denied (files)</option><option value="access.denied">Denied (refused requests)</option><option value="directory.*">Directory</option><option value="volume.*">Storage</option></select></label>
    <label class="lbl">Person<input class="field" bind:value={f.user} placeholder="username" /></label>
    <label class="lbl grow">Path starts with<input class="field mono" bind:value={f.path} placeholder="/shared/finance" /></label>
    <label class="lbl">Through<select class="field" bind:value={f.via}><option value="">Any</option><option value="web">Browser</option><option value="mcp">Claude Code</option><option value="api">API</option><option value="smb">Windows share</option><option value="system">System</option></select></label>
    <button class="btn pri" style="align-self:flex-end">Search</button>
  </form>

  <section class="card">
    {#if !events}<div class="empty">Loading…</div>
    {:else if !events.length}<div class="empty">No entries match.</div>
    {:else}
      <div class="scroll-x"><table class="table">
        <thead><tr><th>When</th><th>Person</th><th>What</th><th>Through</th><th>From</th><th></th></tr></thead>
        <tbody>
          {#each events as e (e.id)}
            {@const [verb, obj, rest] = describe(e)}
            <tr class="click" tabindex="0" aria-expanded={open === e.id} onclick={() => (open = open === e.id ? null : e.id)}
                onkeydown={(ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open = open === e.id ? null : e.id; } }}>
              <td class="muted" style="white-space:nowrap">{when(e.ts)}</td>
              <td style="font-weight:500">{e.actor}</td>
              <td>{#if !e.ok}<span class="tag red" style="margin-right:6px">Refused</span>{/if}{verb} {#if obj}<span class="mono" style="color:var(--amber);font-size:12px">{obj}</span>{/if} <span class="muted">{rest}</span></td>
              <td><span class="tag">{VIA[e.via] || e.via}</span></td>
              <td class="mono note">{e.ip || '—'}</td>
              <td><Icon name={open === e.id ? 'chevD' : 'chevR'} size={14} style="color:var(--muted)" /></td>
            </tr>
            {#if open === e.id}
              <tr><td colspan="6" style="background:#0F1012"><pre class="mono" style="margin:0;font-size:12px;color:var(--text-2);white-space:pre-wrap">{JSON.stringify({ action: e.action, target: e.target, ...e.detail }, null, 2)}</pre></td></tr>
            {/if}
          {/each}
        </tbody>
      </table></div>
    {/if}
  </section>
</div>

<style>
  .filters { padding: 16px 18px; display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end; }
  .filters .lbl { min-width: 160px; }
</style>
