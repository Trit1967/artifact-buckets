<script>
  import { api } from '../lib/api.js';
  import { session } from '../lib/session.svelte.js';
  import { toast, fail } from '../lib/toast.svelte.js';
  import { ago } from '../lib/format.js';
  import Icon from '../lib/Icon.svelte';
  import Copy from '../lib/Copy.svelte';

  let d = $state(null);
  let adGroups = $state(null);
  let adError = $state('');
  let busy = $state(false);

  async function load() {
    try {
      d = await api.get('/api/directory');
      if (d.ad.configured && session.isAdmin) {
        api.get('/api/directory/ad-groups').then((x) => { adGroups = x.groups; adError = ''; }).catch((e) => (adError = e.message));
      }
    } catch (e) { fail(e); }
  }
  load();
  async function sync() {
    busy = true;
    try { const s = await api.post('/api/directory/sync'); toast(`Synced ${s.groups} groups and ${s.users} people. ${s.created} new, ${s.suspended} suspended, ${s.reactivated} back.${s.warnings?.length ? ` ${s.warnings.length} need a look.` : ''}`); load(); } catch (e) { fail(e); } finally { busy = false; }
  }
  async function map(g) {
    try { await api.post('/api/directory/map', { ad_group: g.cn }); toast(`${g.cn} will sync. Running a sync now.`); await sync(); } catch (e) { fail(e); }
  }
  const prefix = $derived(d?.ad.group_prefix || 'AB-');
</script>

<div class="page">
  <header class="page-head">
    <div><h1>Directory</h1><p class="sub">People and groups come from Active Directory, read-only. Sign-in in the browser goes through Entra ID; the Windows share uses the domain join.</p></div>
    {#if session.isAdmin && d?.ad.configured}<button class="btn pri" onclick={sync} disabled={busy}><Icon name="refresh" />{busy ? 'Syncing…' : 'Sync now'}</button>{/if}
  </header>

  {#if !d}<div class="card empty">Loading…</div>{:else}
  <section class="tiles">
    <div class="card tile">
      <div class="row" style="justify-content:space-between"><span class="row" style="gap:10px"><Icon name="building" /><h2>Active Directory</h2></span><span class="tag {d.ad.configured ? 'green' : ''}">{d.ad.configured ? 'Connected' : 'Not set up'}</span></div>
      {#if d.ad.configured}
        <div>
          <div class="kv"><span>Server</span><span class="mono">{d.ad.url}</span></div>
          <div class="kv"><span>Search base</span><span class="mono" style="word-break:break-all">{d.ad.base_dn}</span></div>
          <div class="kv"><span>Groups synced</span><span>Names starting <span class="mono">{prefix}</span>{#if d.ad.extra_groups?.length}, plus {d.ad.extra_groups.join(', ')}{/if}</span></div>
          <div class="kv"><span>Last sync</span><span>{d.ad.last_sync ? `${ago(d.ad.last_sync.at)} · ${d.ad.last_sync.users} people, ${d.ad.last_sync.groups} groups` : 'Never'}</span></div>
          {#if d.ad.last_sync?.warnings?.length}
            <details class="warn"><summary>{d.ad.last_sync.warnings.length} {d.ad.last_sync.warnings.length === 1 ? 'thing' : 'things'} from the last sync need a look</summary>
              <ul>{#each d.ad.last_sync.warnings as w}<li class="mono">{w}</li>{/each}</ul></details>
          {/if}
          <div class="kv"><span>Every</span><span>15 minutes, and on demand</span></div>
        </div>
      {:else}
        <p class="note" style="margin:0">Set these in the app's environment and restart. Use a read-only service account.</p>
        <pre class="code">AB_LDAP_URL=ldaps://dc01.lab.local
AB_LDAP_BIND_DN=CN=svc-buckets,OU=Service,DC=lab,DC=local
AB_LDAP_BIND_PASSWORD=…
AB_LDAP_BASE_DN=DC=lab,DC=local</pre>
      {/if}
    </div>
    <div class="card tile">
      <div class="row" style="justify-content:space-between"><span class="row" style="gap:10px"><Icon name="lock" /><h2>Entra ID sign-in</h2></span><span class="tag {d.entra.configured ? 'green' : ''}">{d.entra.configured ? 'On' : 'Not set up'}</span></div>
      {#if d.entra.configured}
        <div>
          <div class="kv"><span>Issuer</span><span class="mono" style="word-break:break-all">{d.entra.issuer}</span></div>
          <div class="kv"><span>App (client) ID</span><span class="mono">{d.entra.client_id}</span></div>
          <div class="kv"><span>Matches people by</span><span>On-prem SID, then UPN or email</span></div>
        </div>
      {:else}
        <p class="note" style="margin:0">Register an app in Entra with this redirect URI, then set <span class="mono">AB_OIDC_ISSUER</span>, <span class="mono">AB_OIDC_CLIENT_ID</span> and <span class="mono">AB_OIDC_CLIENT_SECRET</span>. Add the optional claim <span class="mono">onprem_sid</span>.</p>
        <Copy text={`${session.base_url}/auth/oidc/callback`} />
      {/if}
    </div>
    <div class="card tile">
      <div class="row" style="justify-content:space-between"><span class="row" style="gap:10px"><Icon name="windows" /><h2>Windows share</h2></span><span class="tag {d.smb.enabled ? 'green' : ''}">{d.smb.enabled ? 'Domain member' : 'Off'}</span></div>
      <div>
        <div class="kv"><span>Server</span><span class="mono">\\{d.smb.host}</span></div>
        <div class="kv"><span>Domain</span><span class="mono">{d.ad.domain || '—'}</span></div>
        <div class="kv"><span>ID mapping</span><span>rid, from <span class="mono">{d.smb.idmap_base}</span></span></div>
      </div>
      <a href="/smb" style="font-size:13px">Shares and permissions</a>
    </div>
  </section>

  {#if d.ad.configured && session.isAdmin}
    <section class="card">
      <div class="hd"><div style="display:flex;flex-direction:column;gap:4px"><h2>AD groups</h2><span class="note">Groups starting <span class="mono">{prefix}</span> sync on their own. Pick others to bring them in too.</span></div></div>
      {#if adError}<div class="empty err">Couldn't read AD groups: {adError}</div>
      {:else if !adGroups}<div class="empty">Reading AD…</div>
      {:else}
        <div class="scroll-x"><table class="table">
          <thead><tr><th>AD group</th><th>Members</th><th>In Artifact Buckets</th><th></th></tr></thead>
          <tbody>
            {#each adGroups as g}
              {@const auto = g.cn.toLowerCase().startsWith(prefix.toLowerCase())}
              <tr>
                <td><span style="font-weight:500">{g.cn}</span>{#if g.description}<div class="note">{g.description}</div>{/if}</td>
                <td class="mono">{g.members}</td>
                <td>{#if g.mapped}<span class="tag green">Synced</span>{:else if auto}<span class="tag amber">Next sync</span>{:else}<span class="note">—</span>{/if}</td>
                <td style="text-align:right">{#if !g.mapped && !auto}<button class="btn sm" onclick={() => map(g)}>Use as group</button>{/if}</td>
              </tr>
            {/each}
          </tbody>
        </table></div>
      {/if}
    </section>
  {/if}

  <section class="card">
    <div class="hd"><h2>Synced groups</h2><span class="note">{d.groups.filter((g) => g.source === 'ad').length} from AD, {d.groups.filter((g) => g.source !== 'ad').length} local</span></div>
    <div class="scroll-x"><table class="table">
      <thead><tr><th>Group</th><th>Source</th><th>Members</th><th>Folder</th><th>SID</th></tr></thead>
      <tbody>
        {#each d.groups as g}
          <tr class="click" onclick={() => (location.href = `/groups?id=${g.id}`)}>
            <td style="font-weight:500"><a class="rowlink" href={`/groups?id=${g.id}`} onclick={(ev) => ev.stopPropagation()}>{g.name}</a></td>
            <td>{#if g.source === 'ad'}<span class="tag blue">{g.ad_name}</span>{:else}<span class="tag">Local</span>{/if}</td>
            <td class="mono">{g.members}</td>
            <td class="mono note">{g.folder}</td>
            <td class="mono note">{g.ad_sid || '—'}</td>
          </tr>
        {:else}<tr><td colspan="5"><div class="empty">No groups yet.</div></td></tr>{/each}
      </tbody>
    </table></div>
  </section>

  <section class="duo">
    <div class="card pad">
      <h2>How people are created</h2>
      <ol class="steps">
        <li>Someone is added to an <span class="mono">{prefix}</span> group in AD, for example <span class="mono">{prefix}Platform</span>.</li>
        <li>The next sync creates their account and home folder, and the group <span class="mono">platform</span> with <span class="mono">/shared/platform</span>.</li>
        <li><span class="mono">{d.ad.admin_group}</span> members are admins; <span class="mono">{d.ad.auditor_group}</span> members are auditors.</li>
        <li>Disabled in AD, or out of every synced group: suspended here, signed out everywhere. Back in: active again, unless an admin suspended them by hand.</li>
        <li>People are matched to AD by their SID, never by name alone. If an AD name is already taken by another account, the person gets a separate account and the sync says so.</li>
      </ol>
    </div>
    <div class="card pad">
      <h2>One person, three ways in</h2>
      <div class="ways">
        <div><Icon name="lock" /><span><b>Browser</b>: Entra ID single sign-on, MFA from your tenant.</span></div>
        <div><Icon name="sparkle" /><span><b>Claude Code</b>: signs in through the same browser page, gets its own revocable device.</span></div>
        <div><Icon name="windows" /><span><b>Windows</b>: Kerberos to the domain-joined share, no extra password.</span></div>
      </div>
      <p class="note" style="margin:0">All three see the same folders with the same permissions, because they all check the same grants.</p>
    </div>
  </section>
  {/if}
</div>

<style>
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; }
  .tile { padding: 20px 22px; display: flex; flex-direction: column; gap: 14px; }
  .hd { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 18px 22px; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
  .duo { display: flex; flex-wrap: wrap; gap: 16px; }
  .duo > * { flex: 1 1 360px; }
  .pad { padding: 22px 24px; display: flex; flex-direction: column; gap: 14px; }
  .steps { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 10px; font-size: 13px; line-height: 1.55; color: var(--text-2); }
  .ways { display: flex; flex-direction: column; gap: 12px; font-size: 13px; line-height: 1.5; color: var(--text-2); }
  .ways > div { display: flex; gap: 12px; align-items: flex-start; }
  .ways :global(.ic) { color: var(--amber); margin-top: 2px; }
  .ways b { color: var(--text); font-weight: 500; }
  .warn { font-size: 12.5px; border: 1px solid var(--line); border-radius: 8px; padding: 8px 12px; }
  .warn summary { cursor: pointer; color: var(--amber); }
  .warn ul { margin: 8px 0 0; padding-left: 18px; display: flex; flex-direction: column; gap: 4px; color: var(--muted); }
</style>
