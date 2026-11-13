<script>
  import { api } from './api.js';
  import { session } from './session.svelte.js';
  import { toast, fail } from './toast.svelte.js';
  import Modal from './Modal.svelte';
  import Icon from './Icon.svelte';
  import Avatar from './Avatar.svelte';
  import RoleTag from './RoleTag.svelte';
  import { ago } from './format.js';

  let { path, canShare, onclose, onchange } = $props();
  let access = $state([]);
  let loading = $state(true);
  let kind = $state('group');
  let name = $state('');
  let role = $state('viewer');
  let groups = $state([]);
  let users = $state([]);
  let busy = $state(false);

  async function load() {
    loading = true;
    try { access = (await api.get('/api/fs/access', { path })).access; } catch (e) { fail(e); } finally { loading = false; }
  }
  load();
  api.get('/api/groups').then((d) => (groups = d.groups)).catch(() => {});
  if (session.isAuditor) api.get('/api/users').then((d) => (users = d.users)).catch(() => {});

  async function add(e) {
    e.preventDefault();
    if (!name.trim()) return;
    busy = true;
    try {
      await api.post('/api/fs/share', { path, kind, name: name.trim(), role });
      toast(`${name} can now ${role === 'viewer' ? 'open' : 'edit'} ${path}`);
      name = '';
      await load();
      onchange?.();
    } catch (e) { fail(e); } finally { busy = false; }
  }

  async function remove(a) {
    try {
      await api.del(`/api/grants/${a.grant_id}`);
      toast(`Removed ${a.principal.name}`);
      await load();
      onchange?.();
    } catch (e) { fail(e); }
  }
</script>

<Modal title="Who can open {path.split('/').pop() || '/'}" {onclose} width={600}>
  <div class="mono note" style="margin-top:-6px">{path}</div>
  {#if loading}
    <div class="empty">Loading…</div>
  {:else}
    <div class="list">
      {#each access as a}
        <div class="row" style="gap:12px;padding:10px 0;border-bottom:1px solid var(--line-soft)">
          {#if a.principal.type === 'group'}
            <Avatar name={a.principal.name} size={30} square />
          {:else if a.principal.type === 'role'}
            <span class="avatar" style="background:var(--amber-bg);color:var(--amber);border-radius:8px"><Icon name="shield" size={14} /></span>
          {:else}
            <Avatar name={a.principal.display_name || a.principal.name} size={30} />
          {/if}
          <span class="grow" style="display:flex;flex-direction:column;gap:2px">
            <span style="font-size:13px;font-weight:500">
              {a.principal.type === 'role' ? 'Admins' : a.principal.display_name || a.principal.name}
              {#if a.principal.type === 'group'}<span class="muted" style="font-weight:400"> · {a.principal.members} {a.principal.members === 1 ? 'person' : 'people'}</span>{/if}
            </span>
            <span class="note">
              {#if a.principal.type === 'role'}Owner everywhere by system role
              {:else if a.inherited}From <span class="mono">{a.via}</span>{:else}Set on this folder{/if}
              {#if a.granted_at} · {ago(a.granted_at)}{/if}
            </span>
          </span>
          <RoleTag role={a.role} />
          {#if canShare && a.grant_id && !a.inherited}
            <button class="btn icon sm" aria-label="Remove {a.principal.name}" onclick={() => remove(a)}><Icon name="x" size={14} /></button>
          {:else}
            <span style="width:30px"></span>
          {/if}
        </div>
      {/each}
    </div>
  {/if}
  {#if canShare}
    <form class="add" onsubmit={add}>
      <span class="sec">Add access</span>
      <div class="row wrap" style="gap:8px">
        <select class="field" style="width:110px" bind:value={kind} aria-label="Kind"><option value="group">Group</option><option value="user">Person</option></select>
        <input class="field grow" style="min-width:160px" list="share-names" bind:value={name} placeholder={kind === 'group' ? 'Group name' : 'Username'} aria-label="Name" />
        <datalist id="share-names">
          {#if kind === 'group'}{#each groups as g}<option value={g.name}>{g.description}</option>{/each}
          {:else}{#each users as u}<option value={u.username}>{u.display_name}</option>{/each}{/if}
        </datalist>
        <select class="field" style="width:110px" bind:value={role} aria-label="Role"><option value="viewer">Viewer</option><option value="editor">Editor</option><option value="owner">Owner</option></select>
        <button class="btn pri" disabled={busy || !name.trim()}>Share</button>
      </div>
      <p class="note" style="margin:0">Applies to this folder and everything inside it. Viewer can open and download. Editor can also save, rename and delete. Owner can also share.</p>
    </form>
  {:else}
    <p class="note" style="margin:0">Only an Owner of this folder can change who has access.</p>
  {/if}
</Modal>

<style>
  .add { display: flex; flex-direction: column; gap: 10px; padding: 14px; border-radius: 10px; background: var(--card-2); border: 1px solid var(--line); }
</style>
