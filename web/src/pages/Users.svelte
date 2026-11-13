<script>
  import { api } from '../lib/api.js';
  import { session } from '../lib/session.svelte.js';
  import { route } from '../lib/router.svelte.js';
  import { toast, fail } from '../lib/toast.svelte.js';
  import { bytes, ago } from '../lib/format.js';
  import Icon from '../lib/Icon.svelte';
  import Modal from '../lib/Modal.svelte';
  import Avatar from '../lib/Avatar.svelte';
  import RoleTag from '../lib/RoleTag.svelte';

  let users = $state(null);
  let groups = $state([]);
  let q = $state('');
  let filter = $state('all');
  let dialog = $state(route.query.get('new') && session.isAdmin ? { kind: 'new' } : null);
  let form = $state(blank());
  let edit = $state(null);
  function blank() { return { username: '', display_name: '', email: '', password: '', system_role: 'member', groups: [] }; }

  async function load() {
    try {
      users = (await api.get('/api/users')).users;
      groups = (await api.get('/api/groups')).groups.filter((g) => g.source === 'local');
    } catch (e) { fail(e); }
  }
  load();

  const shown = $derived((users || []).filter((u) => {
    if (filter === 'admins' && u.system_role === 'member') return false;
    if (filter === 'suspended' && u.status !== 'suspended') return false;
    if (filter === 'ad' && u.source !== 'ad') return false;
    const s = `${u.username} ${u.display_name} ${u.email || ''} ${u.groups.join(' ')}`.toLowerCase();
    return !q || s.includes(q.toLowerCase());
  }));

  async function create(e) {
    e.preventDefault();
    try { await api.post('/api/users', { ...form, email: form.email || null }); toast(`Added ${form.username}. Their home folder is ready.`); dialog = null; form = blank(); load(); } catch (e) { fail(e); }
  }
  async function patch(u, body, msg) {
    try { await api.patch(`/api/users/${u.id}`, body); toast(msg); dialog = null; load(); } catch (e) { fail(e); }
  }
  function openEdit(u) {
    edit = { role: u.system_role, quota: u.quota_bytes ? +(u.quota_bytes / 1024 ** 3).toFixed(1) : '', password: '' };
    dialog = { kind: 'edit', user: u };
  }
  async function saveEdit(e) {
    e.preventDefault();
    const u = dialog.user;
    const body = {};
    if (edit.role !== u.system_role) body.system_role = edit.role;
    if (edit.quota !== '' && Math.round(edit.quota * 1024 ** 3) !== u.quota_bytes) body.quota_bytes = Math.round(edit.quota * 1024 ** 3);
    if (edit.password) body.password = edit.password;
    if (!Object.keys(body).length) { dialog = null; return; }
    patch(u, body, `Saved changes for ${u.display_name}`);
  }
  async function sync() {
    try { const s = await api.post('/api/directory/sync'); toast(`Directory synced: ${s.users} people, ${s.created} new, ${s.suspended} suspended`); load(); } catch (e) { fail(e); }
  }
</script>

<div class="page">
  <header class="page-head">
    <div><h1>Users</h1><p class="sub">Everyone with an account. People from Active Directory arrive by sync; local accounts are for testing and service use.</p></div>
    <div class="row wrap">
      {#if session.isAdmin}
        <button class="btn" onclick={sync}><Icon name="refresh" />Sync directory</button>
        <button class="btn pri" onclick={() => { form = blank(); dialog = { kind: 'new' }; }}><Icon name="plus" />Add person</button>
      {/if}
    </div>
  </header>

  <div class="row wrap" style="gap:12px">
    <label style="position:relative;display:block;width:300px;max-width:100%">
      <span class="sr">Search people</span>
      <Icon name="search" style="position:absolute;left:12px;top:10px;color:var(--faint)" />
      <input class="field" type="search" placeholder="Name, email or group" bind:value={q} style="padding-left:36px" />
    </label>
    <div class="segs" role="group" aria-label="Filter">
      {#each [['all', 'Everyone'], ['admins', 'Admins and auditors'], ['ad', 'From AD'], ['suspended', 'Suspended']] as [k, l]}
        <button class="seg" class:on={filter === k} aria-pressed={filter === k} onclick={() => (filter = k)}>{l}</button>
      {/each}
    </div>
    <span class="note" style="margin-left:auto">{shown.length} of {users?.length ?? 0}</span>
  </div>

  <section class="card">
    {#if !users}<div class="empty">Loading…</div>{:else}
    <div class="scroll-x"><table class="table">
      <thead><tr><th>Person</th><th>Groups</th><th>Role</th><th style="min-width:160px">Storage</th><th>Last active</th><th>Status</th>{#if session.isAdmin}<th></th>{/if}</tr></thead>
      <tbody>
        {#each shown as u (u.id)}
          <tr>
            <td>
              <span class="row" style="gap:12px">
                <Avatar name={u.display_name} />
                <span style="display:flex;flex-direction:column;gap:2px;min-width:0">
                  <span style="font-weight:500">{u.display_name}</span>
                  <span class="note">{u.email || u.username}{#if u.source === 'ad'} · <span style="color:var(--blue)">AD</span>{/if}</span>
                </span>
              </span>
            </td>
            <td><span class="row wrap" style="gap:4px">{#each u.groups.slice(0, 3) as g}<span class="tag">{g}</span>{/each}{#if u.groups.length > 3}<span class="note">+{u.groups.length - 3}</span>{/if}{#if !u.groups.length}<span class="note">—</span>{/if}</span></td>
            <td><RoleTag role={u.system_role} /></td>
            <td>
              <div style="display:flex;flex-direction:column;gap:6px">
                <span class="mono note" style="color:var(--text-2)">{bytes(u.used_bytes)} <span class="muted">/ {bytes(u.quota_bytes, 0)}</span></span>
                <div class="meter" style="height:4px"><div style="width:{u.quota_bytes ? Math.min(100, (u.used_bytes / u.quota_bytes) * 100) : 0}%;background:{u.quota_bytes && u.used_bytes / u.quota_bytes > 0.9 ? 'var(--red)' : 'var(--amber)'}"></div></div>
              </div>
            </td>
            <td class="muted" style="white-space:nowrap">{ago(u.last_active)}</td>
            <td>{#if u.status === 'active'}<span class="row" style="gap:7px;font-size:13px"><span class="dot" style="background:var(--green);width:7px;height:7px"></span>Active</span>{:else}<span class="tag red">Suspended</span>{/if}</td>
            {#if session.isAdmin}
              <td style="text-align:right;white-space:nowrap">
                {#if u.id !== session.user.id}
                  <button class="btn sm" onclick={() => openEdit(u)}>Edit</button>
                  {#if u.status === 'active'}
                    <button class="btn sm danger" onclick={() => (dialog = { kind: 'suspend', user: u })}>Suspend</button>
                  {:else}
                    <button class="btn sm" onclick={() => patch(u, { status: 'active' }, `${u.display_name} can sign in again`)}>Reactivate</button>
                  {/if}
                {:else}<span class="note">You</span>{/if}
              </td>
            {/if}
          </tr>
        {:else}
          <tr><td colspan="7"><div class="empty">Nobody matches.</div></td></tr>
        {/each}
      </tbody>
    </table></div>
    {/if}
  </section>
</div>

{#if dialog?.kind === 'new'}
  <Modal title="Add a person" onclose={() => (dialog = null)} width={560}>
    <form onsubmit={create} style="display:flex;flex-direction:column;gap:14px">
      <p class="note" style="margin:0">People in Active Directory don't need adding: put them in an <span class="mono">AB-</span> group and sync. Use this for local and test accounts.</p>
      <div class="row wrap" style="gap:12px">
        <label class="lbl grow">Username<input class="field mono" bind:value={form.username} required pattern="[a-z0-9][a-z0-9._\-]*" autocapitalize="none" /></label>
        <label class="lbl grow">Display name<input class="field" bind:value={form.display_name} /></label>
      </div>
      <label class="lbl">Email (optional)<input class="field" type="email" bind:value={form.email} /></label>
      <label class="lbl">Temporary password<input class="field" type="password" minlength="12" bind:value={form.password} required autocomplete="new-password" /><span class="note">At least 12 characters.</span></label>
      <label class="lbl">Role<select class="field" bind:value={form.system_role}><option value="member">Member</option><option value="auditor">Auditor: reads the audit log and access reports, not files</option><option value="admin">Admin: Owner of every folder</option></select></label>
      {#if groups.length}
        <fieldset class="gs"><legend class="lbl">Local groups</legend>
          {#each groups as g}<label class="ck"><input type="checkbox" value={g.name} bind:group={form.groups} />{g.name}</label>{/each}
        </fieldset>
      {/if}
      <div class="row" style="justify-content:flex-end"><button type="button" class="btn" onclick={() => (dialog = null)}>Cancel</button><button class="btn pri">Add person</button></div>
    </form>
  </Modal>
{:else if dialog?.kind === 'edit'}
  <Modal title="Edit {dialog.user.display_name}" onclose={() => (dialog = null)}>
    <form onsubmit={saveEdit} style="display:flex;flex-direction:column;gap:14px">
      <label class="lbl">Role<select class="field" bind:value={edit.role} disabled={dialog.user.source === 'ad'}><option value="member">Member</option><option value="auditor">Auditor</option><option value="admin">Admin</option></select>
        {#if dialog.user.source === 'ad'}<span class="note">Set by AD groups AB-Admins and AB-Auditors.</span>{/if}</label>
      <label class="lbl">Storage quota (GB)<input class="field" type="number" min="0.1" step="0.1" bind:value={edit.quota} /></label>
      {#if dialog.user.source !== 'ad'}<label class="lbl">New password (leave empty to keep)<input class="field" type="password" minlength="12" bind:value={edit.password} autocomplete="new-password" /></label>{/if}
      <div class="row" style="justify-content:flex-end"><button type="button" class="btn" onclick={() => (dialog = null)}>Cancel</button><button class="btn pri">Save</button></div>
    </form>
  </Modal>
{:else if dialog?.kind === 'suspend'}
  <Modal title="Suspend {dialog.user.display_name}?" onclose={() => (dialog = null)}>
    <p style="margin:0;font-size:13px;line-height:1.6;color:var(--text-2)">They're signed out everywhere at once: the browser, every Claude Code computer and every personal token. Their files stay where they are. You can reactivate them later.{dialog.user.source === 'ad' ? ' AD sync won’t undo this.' : ''}</p>
    <div class="row" style="justify-content:flex-end"><button class="btn" onclick={() => (dialog = null)}>Cancel</button><button class="btn pri" onclick={() => patch(dialog.user, { status: 'suspended' }, `${dialog.user.display_name} is suspended`)}>Suspend</button></div>
  </Modal>
{/if}

<style>
  .segs { display: flex; gap: 2px; padding: 3px; border-radius: 10px; background: var(--card); border: 1px solid var(--line); flex-wrap: wrap; }
  .gs { border: 1px solid var(--line); border-radius: 10px; padding: 10px 14px; display: flex; flex-wrap: wrap; gap: 8px 18px; margin: 0; }
  .gs legend { padding: 0 6px; }
  .ck { display: flex; gap: 8px; align-items: center; font-size: 13px; }
  .ck input { accent-color: var(--amber); }
</style>
