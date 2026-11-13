<script>
  import { api } from '../lib/api.js';
  import { session } from '../lib/session.svelte.js';
  import { route, navigate } from '../lib/router.svelte.js';
  import { toast, fail } from '../lib/toast.svelte.js';
  import { ago } from '../lib/format.js';
  import Icon from '../lib/Icon.svelte';
  import Modal from '../lib/Modal.svelte';
  import Avatar from '../lib/Avatar.svelte';
  import RoleTag from '../lib/RoleTag.svelte';

  let groups = $state(null);
  let g = $state(null);
  let dialog = $state(null);
  let form = $state({});
  let users = $state([]);
  let check = $state({ path: '', user: '' });
  let checkOut = $state(null);
  const selId = $derived(Number(route.query.get('id')) || null);

  async function load() {
    try {
      groups = (await api.get('/api/groups')).groups;
      if (!selId && groups.length) navigate(`/groups?id=${groups[0].id}`, true);
    } catch (e) { fail(e); }
  }
  load();
  if (session.isAdmin) api.get('/api/users').then((d) => (users = d.users)).catch(() => {});
  $effect(() => { const id = selId; g = null; if (id) api.get(`/api/groups/${id}`).then((x) => (g = x)).catch(fail); });
  const reload = async () => { g = await api.get(`/api/groups/${selId}`); load(); };

  async function createGroup(e) {
    e.preventDefault();
    try { const x = await api.post('/api/groups', form); dialog = null; toast(`Created ${x.name} with folder ${x.folder}`); await load(); navigate(`/groups?id=${x.id}`); } catch (e) { fail(e); }
  }
  async function addMember(e) {
    e.preventDefault();
    try { g = await api.post(`/api/groups/${g.id}/members`, { username: form.username }); form.username = ''; toast('Added'); load(); } catch (e) { fail(e); }
  }
  async function removeMember(m) {
    try { g = await api.del(`/api/groups/${g.id}/members/${encodeURIComponent(m.username)}`); toast(`Removed ${m.display_name}`); load(); } catch (e) { fail(e); }
  }
  async function addGrant(e) {
    e.preventDefault();
    try { await api.post('/api/fs/share', { path: form.path, kind: 'group', name: g.name, role: form.role }); dialog = null; toast(`${g.name} is now ${form.role} on ${form.path}`); reload(); } catch (e) { fail(e); }
  }
  async function removeGrant(x) {
    try { await api.del(`/api/grants/${x.id}`); toast(`Removed ${g.name} from ${x.path}`); reload(); } catch (e) { fail(e); }
  }
  async function runCheck(e) {
    e.preventDefault();
    try { checkOut = await api.get('/api/access-check', check.user.trim() ? { path: check.path, user: check.user.trim() } : { path: check.path }); } catch (e) { fail(e); }
  }
</script>

<div class="page">
  <header class="page-head">
    <div><h1>{session.isAuditor ? 'Groups and access' : 'My groups'}</h1><p class="sub">Groups get roles on folders. A role applies to the folder and everything inside it, and the highest role a person has wins.</p></div>
    {#if session.isAdmin}<button class="btn pri" onclick={() => { form = { name: '', description: '' }; dialog = 'new'; }}><Icon name="plus" />New group</button>{/if}
  </header>

  <div class="layout">
    <nav class="card list" aria-label="Groups">
      {#if !groups}<div class="empty">Loading…</div>
      {:else if !groups.length}<div class="empty">No groups yet.</div>
      {:else}
        {#each groups as x}
          <a class="gi" class:on={x.id === selId} href={`/groups?id=${x.id}`}>
            <Avatar name={x.name} size={30} square />
            <span class="grow" style="display:flex;flex-direction:column;gap:2px;min-width:0">
              <span style="font-weight:500;font-size:13px">{x.name}</span>
              <span class="note">{x.members} {x.members === 1 ? 'person' : 'people'} · {x.grants.length} folder{x.grants.length === 1 ? '' : 's'}</span>
            </span>
            {#if x.source === 'ad'}<span class="tag blue">AD</span>{/if}
          </a>
        {/each}
      {/if}
    </nav>

    <section class="detail">
      {#if g}
        <div class="card pad">
          <div class="row" style="gap:14px;align-items:flex-start">
            <Avatar name={g.name} size={44} square />
            <div class="grow" style="display:flex;flex-direction:column;gap:6px">
              <div class="row" style="gap:10px"><h2 style="font-size:20px;letter-spacing:-0.02em">{g.name}</h2>{#if g.source === 'ad'}<span class="tag blue">Active Directory · {g.ad_name}</span>{:else}<span class="tag">Local</span>{/if}</div>
              <span class="note" style="font-size:13px">{g.description || 'No description'}</span>
            </div>
            <a class="btn" href={`/files?path=${encodeURIComponent(g.folder)}`}><Icon name="folder" />Team folder</a>
          </div>
        </div>

        <div class="card">
          <div class="hd"><h2>Folder access</h2>{#if session.isAdmin}<button class="btn sm" onclick={() => { form = { path: g.folder, role: 'viewer' }; dialog = 'grant'; }}><Icon name="plus" size={14} />Add folder</button>{/if}</div>
          {#if g.grants.length}
            <div class="scroll-x"><table class="table">
              <thead><tr><th>Folder</th><th>Role</th><th>Granted</th>{#if session.isAdmin}<th></th>{/if}</tr></thead>
              <tbody>
                {#each g.grants as x}
                  <tr>
                    <td class="mono"><a href={`/files?path=${encodeURIComponent(x.path)}`} style="color:var(--text)">{x.path}</a></td>
                    <td><RoleTag role={x.role} /></td>
                    <td class="note">{ago(x.granted_at)} by {x.granted_by}</td>
                    {#if session.isAdmin}<td style="text-align:right"><button class="btn sm" onclick={() => removeGrant(x)}>Remove</button></td>{/if}
                  </tr>
                {/each}
              </tbody>
            </table></div>
          {:else}<div class="empty">This group has no folder access yet.</div>{/if}
        </div>

        <div class="card">
          <div class="hd"><h2>People</h2><span class="note">{g.members}</span></div>
          {#if session.isAdmin && g.source === 'local'}
            <form class="row" style="gap:8px;padding:14px 22px;border-bottom:1px solid var(--line-soft)" onsubmit={addMember}>
              <input class="field grow" list="all-users" placeholder="Add by username" bind:value={form.username} aria-label="Username" />
              <datalist id="all-users">{#each users as u}<option value={u.username}>{u.display_name}</option>{/each}</datalist>
              <button class="btn" disabled={!form.username}>Add</button>
            </form>
          {:else if g.source === 'ad'}
            <div class="note" style="padding:12px 22px;border-bottom:1px solid var(--line-soft)">Members come from Active Directory. Change them there; the next sync picks it up.</div>
          {/if}
          {#each g.member_list as m}
            <div class="mem">
              <Avatar name={m.display_name} size={28} />
              <span class="grow" style="display:flex;flex-direction:column;gap:2px"><span style="font-size:13px;font-weight:500">{m.display_name}</span><span class="note">{m.email || m.username}</span></span>
              <span class="note">added {ago(m.added_at).toLowerCase()}</span>
              {#if session.isAdmin && g.source === 'local'}<button class="btn icon sm" aria-label="Remove {m.display_name}" onclick={() => removeMember(m)}><Icon name="x" size={14} /></button>{/if}
            </div>
          {:else}<div class="empty">Nobody in this group yet.</div>{/each}
        </div>
      {:else if selId}<div class="card empty">Loading…</div>{/if}

      <div class="card pad" style="display:flex;flex-direction:column;gap:14px">
        <h2>What each role can do</h2>
        <div class="scroll-x"><table class="table roles">
          <thead><tr><th>On a folder and its contents</th><th>Viewer</th><th>Editor</th><th>Owner</th></tr></thead>
          <tbody>
            {#each [['See it, open files, download', 1, 1, 1], ['Save, upload, new versions', 0, 1, 1], ['Rename, move, delete to trash', 0, 1, 1], ['Share and change who has access', 0, 0, 1]] as [l, ...r]}
              <tr><td>{l}</td>{#each r as y}<td>{#if y}<Icon name="check" style="color:var(--green)" />{:else}<span class="muted">—</span>{/if}</td>{/each}</tr>
            {/each}
          </tbody>
        </table></div>
        <p class="note" style="margin:0">Admins are Owner everywhere. Auditors read the audit log and these reports but can't open files. On the Windows share, Viewer is Read and Editor or Owner is Modify.</p>
      </div>

      {#if session.isAuditor}
        <form class="card pad" style="display:flex;flex-direction:column;gap:12px" onsubmit={runCheck}>
          <h2>Who can reach a folder?</h2>
          <div class="row" style="gap:8px;flex-wrap:wrap"><input class="field mono grow" placeholder="/shared/finance/q3" bind:value={check.path} required aria-label="Folder path" /><input class="field mono" style="width:150px" placeholder="person (optional)" bind:value={check.user} aria-label="Person" /><button class="btn">Check</button></div>
          {#if checkOut?.person}
            {@const p = checkOut.person}
            <div class="verdict" class:yes={p.role}>
              <strong class="mono">{p.username}</strong>
              {#if p.status !== 'active'} is suspended and can't reach it.
              {:else if p.reason === 'admin'} is an admin, so Owner everywhere.
              {:else if p.role} can {p.role === 'viewer' ? 'open' : 'open and change'} it as <RoleTag role={p.role} />{#if p.grants[0]}<span class="note"> from {p.grants[0].principal.type === 'group' ? `group ${p.grants[0].principal.name}` : 'a direct share'} on <span class="mono">{p.grants[0].via}</span></span>{/if}
              {:else} can't reach it. No grant on this folder or above includes them.{/if}
            </div>
          {/if}
          {#if checkOut}
            {#each checkOut.access as a}
              <div class="row" style="gap:10px;font-size:13px;padding:6px 0">
                <span class="grow">{a.principal.type === 'role' ? 'Admins' : a.principal.name}{#if a.principal.type === 'group'}<span class="note">&nbsp;· group of {a.principal.members}</span>{/if}</span>
                <span class="note mono">{a.via}</span><RoleTag role={a.role} />
              </div>
            {/each}
          {/if}
        </form>
      {/if}
    </section>
  </div>
</div>

{#if dialog === 'new'}
  <Modal title="New group" onclose={() => (dialog = null)}>
    <form onsubmit={createGroup} style="display:flex;flex-direction:column;gap:14px">
      <label class="lbl">Name<input class="field mono" bind:value={form.name} required pattern="[a-z0-9][a-z0-9_\-]*" placeholder="platform-eng" /></label>
      <label class="lbl">Description<input class="field" bind:value={form.description} /></label>
      <p class="note" style="margin:0">Creates <span class="mono">/shared/{form.name || '<name>'}</span> with Editor for members. For AD-backed groups, create <span class="mono">AB-{form.name || '<name>'}</span> in AD instead and sync.</p>
      <div class="row" style="justify-content:flex-end"><button type="button" class="btn" onclick={() => (dialog = null)}>Cancel</button><button class="btn pri">Create</button></div>
    </form>
  </Modal>
{:else if dialog === 'grant'}
  <Modal title="Give {g.name} access to a folder" onclose={() => (dialog = null)}>
    <form onsubmit={addGrant} style="display:flex;flex-direction:column;gap:14px">
      <label class="lbl">Folder<input class="field mono" bind:value={form.path} required /></label>
      <label class="lbl">Role<select class="field" bind:value={form.role}><option value="viewer">Viewer</option><option value="editor">Editor</option><option value="owner">Owner</option></select></label>
      <div class="row" style="justify-content:flex-end"><button type="button" class="btn" onclick={() => (dialog = null)}>Cancel</button><button class="btn pri">Give access</button></div>
    </form>
  </Modal>
{/if}

<style>
  .layout { display: flex; flex-wrap: wrap; gap: 16px; align-items: flex-start; }
  .list { flex: 0 0 280px; padding: 8px; display: flex; flex-direction: column; gap: 2px; position: sticky; top: 20px; max-height: calc(100vh - 40px); overflow-y: auto; }
  .gi { display: flex; align-items: center; gap: 12px; padding: 10px; border-radius: 9px; color: var(--text); }
  .gi:hover { background: var(--card-2); color: var(--text); }
  .gi.on { background: var(--raise); box-shadow: inset 0 0 0 1px #292C32; }
  .detail { flex: 999 1 520px; min-width: 0; display: flex; flex-direction: column; gap: 16px; }
  .pad { padding: 22px; }
  .hd { display: flex; justify-content: space-between; align-items: center; padding: 16px 22px; border-bottom: 1px solid var(--line); }
  .mem { display: flex; align-items: center; gap: 12px; padding: 10px 22px; border-bottom: 1px solid var(--line-soft); }
  .mem:last-child { border-bottom: 0; }
  .roles td, .roles th { padding: 10px 12px; }
  .roles td:not(:first-child), .roles th:not(:first-child) { text-align: center; width: 90px; }
  .roles :global(.ic) { display: inline-block; }
  @media (max-width: 900px) { .list { position: static; flex: 1 1 100%; max-height: none; } }
  .verdict { font-size: 13px; padding: 10px 12px; border-radius: 8px; background: var(--red-bg); border: 1px solid var(--line); display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .verdict.yes { background: var(--green-bg); }
</style>
