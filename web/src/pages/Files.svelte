<script>
  import { api } from '../lib/api.js';
  import { session } from '../lib/session.svelte.js';
  import { route, navigate } from '../lib/router.svelte.js';
  import { toast, fail } from '../lib/toast.svelte.js';
  import { bytes, ago, when, crumbs, ext, extTint, SOURCE } from '../lib/format.js';
  import { markdown } from '../lib/markdown.js';
  import Icon from '../lib/Icon.svelte';
  import Modal from '../lib/Modal.svelte';
  import RoleTag from '../lib/RoleTag.svelte';
  import ShareDialog from '../lib/ShareDialog.svelte';
  import Versions from '../lib/Versions.svelte';

  const me = session.user.username;
  const home = `/users/${me}`;
  const RANK = { viewer: 1, editor: 2, owner: 3 };
  const atLeast = (r, need) => !!r && RANK[r] >= RANK[need];

  let path = $derived(route.query.get('path') || home);
  let selPath = $derived(route.query.get('file'));
  let listing = $state(null);
  let error = $state('');
  let sel = $state(null);       // selected file meta
  let text = $state(null);      // text preview
  let q = $state('');
  let results = $state(null);
  let drop = $state(false);
  let dragging = $state(false);
  let uploads = $state([]);
  let shared = $state([]);
  let others = $state([]);
  let dialog = $state(null);    // {kind, ...}
  let input = $state();

  function open(p, file) {
    const u = new URLSearchParams({ path: p });
    if (file) u.set('file', file);
    navigate(`/files?${u}`);
  }
  const view = (file) => navigate(`/view?path=${encodeURIComponent(file)}`);
  // On narrow screens the side panel would sit below the list, off screen, so open the full viewer instead.
  const pick = (file) => (matchMedia('(max-width: 1100px)').matches ? view(file) : open(path, file));
  let rev = $state(0);

  async function load() {
    error = '';
    try {
      listing = await api.get('/api/fs/list', { path });
    } catch (e) {
      listing = null;
      error = e.status === 404 ? 'This folder doesn’t exist, or you don’t have access to it.' : e.message;
    }
  }

  async function loadTree() {
    if (session.isAuditor && !session.isAdmin) return;  // auditors don't browse files
    try {
      shared = (await api.get('/api/fs/list', { path: '/shared' })).folders;
      others = (await api.get('/api/fs/list', { path: '/users' })).folders.filter((f) => f.name !== me);
    } catch { /* ignore */ }
  }

  $effect(() => { path; results = null; q = ''; load(); });
  $effect(() => { loadTree(); });

  $effect(() => {
    const f = selPath; rev;
    sel = null; text = null;
    if (!f) return;
    api.get('/api/fs/meta', { path: f }).then(async (m) => {
      sel = m;
      if (isText(m) && m.size < 512 * 1024) {
        const r = await fetch(api.download(f), { credentials: 'same-origin' });
        if (r.ok) text = await r.text();
      }
    }).catch(fail);
  });

  const isText = (m) => /^(text\/(plain|markdown|csv|x-)|application\/(json|x-yaml|yaml|x-sh))/.test(m.mime) || /\.(md|txt|json|ya?ml|py|js|ts|sql|csv|log|toml|sh|ini|cfg)$/i.test(m.name);
  const isFrame = (m) => /^(text\/html|image\/svg\+xml|application\/pdf)/.test(m.mime);
  const isImage = (m) => /^image\/(png|jpe?g|gif|webp)/.test(m.mime);

  let timer;
  function onSearch() {
    clearTimeout(timer);
    if (!q.trim()) { results = null; return; }
    timer = setTimeout(async () => {
      try { results = (await api.get('/api/fs/search', { q, folder: path })).results; } catch (e) { fail(e); }
    }, 220);
  }

  const role = $derived(listing?.role);
  const canEdit = $derived(atLeast(role, 'editor'));
  const canShare = $derived(atLeast(role, 'owner'));
  const rows = $derived(results ?? listing?.files ?? []);
  const folders = $derived(results ? [] : listing?.folders ?? []);
  const where = $derived(path === home || path.startsWith(home + '/') ? 'Mine' : path.startsWith('/shared') ? 'Shared' : path.startsWith('/users/') ? (session.isAdmin ? 'People' : 'Shared with me') : path === '/' ? 'All files' : '');
  const shown = $derived(crumbs(path).filter((c) => c.path !== '/users' && c.path !== '/shared').map((c) => ({ ...c, name: c.path === home ? 'Home' : c.name })));
  const trail = $derived(crumbs(path).map((c) => ({ ...c, name: c.path === home ? 'Home' : c.path === '/users' ? 'People' : c.path === '/shared' ? 'Shared' : c.name })));

  async function sendFiles(list) {
    for (const file of list) {
      const item = { name: file.name, size: file.size, state: 'uploading' };
      uploads = [...uploads, item];
      const fd = new FormData();
      fd.set('folder', path);
      fd.set('file', file);
      try {
        await api.post('/api/fs/upload', fd);
        item.state = 'done';
      } catch (e) {
        item.state = 'failed'; item.error = e.message;
      }
      uploads = [...uploads];
    }
    load(); rev++;
    setTimeout(() => (uploads = uploads.filter((u) => u.state === 'failed')), 2500);
  }

  function onDrop(e) {
    e.preventDefault(); dragging = false;
    if (!canEdit) return toast('You can only view this folder', 'err');
    if (e.dataTransfer?.files?.length) sendFiles([...e.dataTransfer.files]);
  }

  async function newFolder(name) {
    try {
      await api.post('/api/fs/mkdir', { path: `${path.replace(/\/$/, '')}/${name}` });
      dialog = null; toast(`Created ${name}`); load(); loadTree();
    } catch (e) { fail(e); }
  }

  async function rename(item, name) {
    const dst = item.path.split('/').slice(0, -1).join('/') + '/' + name;
    try {
      await api.post('/api/fs/move', { src: item.path, dst });
      dialog = null; toast('Renamed');
      if (item.type === 'file') open(path, dst); else if (item.path === path) open(dst); else load();
      loadTree();
    } catch (e) { fail(e); }
  }

  async function move(item, dest) {
    const dst = dest.replace(/\/$/, '') + '/' + item.path.split('/').pop();
    try {
      await api.post('/api/fs/move', { src: item.path, dst });
      dialog = null; toast(`Moved to ${dest}`);
      if (item.path === path) open(dst); else { open(path); load(); }
    } catch (e) { fail(e); }
  }

  async function askDelete(item) {
    try {
      const p = await api.post('/api/fs/delete', { path: item.path, preview: true });
      dialog = { kind: 'delete', item, files: p.files, bytes: p.bytes };
    } catch (e) { fail(e); }
  }

  let fmBtn = $state();
  function closeMenu(focus = true) { fmenu = false; if (focus) fmBtn?.focus(); }
  function menuKeys(e) {
    const items = [...e.currentTarget.querySelectorAll('[role=menuitem]')];
    const i = items.indexOf(document.activeElement);
    if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length].focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
    else if (e.key === 'Home') { e.preventDefault(); items[0].focus(); }
    else if (e.key === 'End') { e.preventDefault(); items[items.length - 1].focus(); }
    else if (e.key === 'Escape') { e.stopPropagation(); closeMenu(); }
    else if (e.key === 'Tab') closeMenu(false);
  }
  function focusFirst(node) { node.querySelector('[role=menuitem]')?.focus(); }

  async function remove(item) {
    try {
      const r = await api.post('/api/fs/delete', { path: item.path, confirm: true });
      dialog = null;
      toast(`Moved to trash${r.files > 1 ? ` (${r.files} files)` : ''}. You can restore it for ${session.trash_days || 30} days.`);
      if (item.path === path) open(path.split('/').slice(0, -1).join('/')); else { open(path); load(); }
      loadTree();
    } catch (e) { fail(e); }
  }

  const folderItem = $derived({ type: 'folder', path, name: path.split('/').pop() });
  const folderEditable = $derived.by(() => {
    const p = path.split('/').filter(Boolean);
    // Homes, team folders and the inbox and projects folders that saves are routed to stay put.
    const fixed = p.length === 3 && ((p[0] === 'users' && ['inbox', 'projects'].includes(p[2])) || (p[0] === 'shared' && p[2] === 'projects'));
    return canEdit && p.length >= 3 && !fixed;
  });
  let fmenu = $state(false);
  let nameInput = $state('');
  let destInput = $state('');
</script>

<svelte:window onclick={(e) => { if (fmenu && !e.target.closest('.fm')) fmenu = false; }} onkeydown={(e) => { if (e.key === 'Escape' && fmenu) closeMenu(); }} ondragover={(e) => { if (e.dataTransfer?.types?.includes('Files')) { e.preventDefault(); dragging = true; } }} ondragleave={(e) => { if (!e.relatedTarget) dragging = false; }} ondragend={() => (dragging = false)} ondrop={onDrop} />

<div class="page">
  <header class="page-head" style="align-items:center">
    <nav aria-label="Breadcrumb" class="crumbs">
      {#if where && shown.length}<span class="muted">{where}</span><Icon name="chevR" size={14} style="color:var(--faint)" />{/if}
      {#if !shown.length}<h1 class="here">{where || 'All files'}</h1>{/if}
      {#each shown as c, i}
        {#if i === shown.length - 1}
          <h1 class="here">{c.name}</h1>
        {:else}
          <a href={`/files?path=${encodeURIComponent(c.path)}`} class="muted">{c.name}</a><Icon name="chevR" size={14} style="color:var(--faint)" />
        {/if}
      {/each}
      {#if role}<RoleTag {role} />{/if}
    </nav>
    <div class="row wrap">
      <label class="search">
        <span class="sr">Search files</span>
        <Icon name="search" style="position:absolute;left:12px;top:10px;color:var(--faint)" />
        <input class="field" type="search" placeholder="Search in this folder" bind:value={q} oninput={onSearch} style="padding-left:36px" />
      </label>
      {#if canShare}<button class="btn" onclick={() => (dialog = { kind: 'share', path, folder: true })}><Icon name="share" />Share</button>{/if}
      {#if folderEditable}
        <div class="fm">
          <button class="btn icon" bind:this={fmBtn} aria-label="Folder actions" aria-haspopup="menu" aria-expanded={fmenu} onclick={() => (fmenu = !fmenu)}><Icon name="more" /></button>
          {#if fmenu}
            <div class="menu card" role="menu" tabindex="-1" onkeydown={menuKeys} use:focusFirst>
              <button role="menuitem" onclick={() => { fmenu = false; nameInput = folderItem.name; dialog = { kind: 'rename', item: folderItem }; }}><Icon name="edit" size={14} />Rename folder</button>
              <button role="menuitem" onclick={() => { fmenu = false; destInput = path.split('/').slice(0, -1).join('/'); dialog = { kind: 'move', item: folderItem }; }}><Icon name="move" size={14} />Move folder</button>
              <button role="menuitem" class="danger" onclick={() => { fmenu = false; askDelete(folderItem); }}><Icon name="trash" size={14} />Delete folder</button>
            </div>
          {/if}
        </div>
      {/if}
      {#if canEdit}
        <button class="btn" onclick={() => { nameInput = ''; dialog = { kind: 'mkdir' }; }}><Icon name="folderPlus" />New folder</button>
        <button class="btn pri" onclick={() => (drop = !drop)}><Icon name="upload" />Upload</button>
      {/if}
    </div>
  </header>

  {#if drop && canEdit}
    <div class="dropzone" class:hot={dragging}>
      <span class="dzicon"><Icon name="upload" size={20} /></span>
      <div class="grow" style="display:flex;flex-direction:column;gap:4px;min-width:220px">
        <span style="font-size:14px;font-weight:500">Drop files here or choose from your computer</span>
        <span class="note">Uploads to <span class="mono">{path}</span>. Up to {bytes(session.max_file_bytes || 500 * 1024 * 1024)} per file. Saving a file with the same name adds a new version.</span>
      </div>
      <input type="file" multiple bind:this={input} hidden onchange={(e) => { sendFiles([...e.target.files]); e.target.value = ''; }} />
      <button class="btn" onclick={() => input.click()}>Choose files</button>
      <button class="btn icon" aria-label="Close upload" onclick={() => (drop = false)}><Icon name="x" /></button>
    </div>
  {/if}
  {#if uploads.length}
    <div class="card" style="padding:10px 16px">
      {#each uploads as u}
        <div class="row" style="font-size:13px;padding:6px 0">
          <Icon name={u.state === 'done' ? 'check' : u.state === 'failed' ? 'alert' : 'upload'} style="color:{u.state === 'done' ? 'var(--green)' : u.state === 'failed' ? 'var(--red)' : 'var(--amber)'}" />
          <span class="grow">{u.name}</span><span class="note">{u.state === 'failed' ? u.error : u.state === 'done' ? 'Saved' : bytes(u.size)}</span>
        </div>
      {/each}
    </div>
  {/if}

  <div class="layout">
    <nav aria-label="Folders" class="card tree">
      <span class="sec" style="padding:4px 10px 8px">Mine</span>
      <a class="leaf" class:on={path === home} href={`/files?path=${encodeURIComponent(home)}`}><Icon name="home" />Home</a>
      <a class="leaf" class:on={path === home + '/inbox'} href={`/files?path=${encodeURIComponent(home + '/inbox')}`}><Icon name="inbox" />Inbox</a>
      <a class="leaf" class:on={path.startsWith(home + '/projects')} href={`/files?path=${encodeURIComponent(home + '/projects')}`}><Icon name="git" />Projects</a>
      <a class="leaf" href="/trash"><Icon name="trash" />Trash</a>
      {#if shared.length}
        <span class="sec" style="padding:18px 10px 8px">Team folders</span>
        {#each shared as f}
          <a class="leaf" class:on={path === f.path || path.startsWith(f.path + '/')} href={`/files?path=${encodeURIComponent(f.path)}`}>
            <Icon name="folder" style={path.startsWith(f.path) ? 'color:var(--amber)' : ''} /><span class="grow">{f.name}</span>
          </a>
        {/each}
      {/if}
      {#if others.length}
        <span class="sec" style="padding:18px 10px 8px">{session.isAdmin ? 'People' : 'Shared with me'}</span>
        {#each others as f}
          <a class="leaf" class:on={path === f.path || path.startsWith(f.path + '/')} href={`/files?path=${encodeURIComponent(f.path)}`}>
            <Icon name="user" /><span class="grow">{f.name}</span>
          </a>
        {/each}
      {/if}
    </nav>

    <section aria-label="Files in folder" class="card listing" class:hot={dragging && canEdit}>
      {#if error}
        <div class="empty">{error}<br /><a href="/files">Back to your home folder</a></div>
      {:else if !listing}
        <div class="empty">Loading…</div>
      {:else}
        <div class="scroll-x"><div style="min-width:520px">
          <div class="grid head"><span class="th">Name</span><span class="th">Saved by</span><span class="th">From</span><span class="th" style="text-align:right">Size</span><span class="th" style="text-align:right">Modified</span></div>
          {#each folders as f (f.path)}
            <button class="grid rowbtn" onclick={() => open(f.path)} title={f.name}>
              <span class="row" style="gap:12px;min-width:0"><span class="fi"><Icon name="folder" /></span><span class="nm">{f.name}</span></span>
              <span class="muted">—</span><span></span><span></span><span></span>
            </button>
          {/each}
          {#each rows as x (x.path)}
            {@const t = extTint(x.name)}
            {@const s = SOURCE[x.source] || [x.source, '#9097A0']}
            <button class="grid rowbtn" class:on={selPath === x.path} onclick={() => pick(x.path)} ondblclick={() => view(x.path)} title={x.name}>
              <span class="row" style="gap:12px;min-width:0">
                <span class="ext mono" style="background:{t[0]};color:{t[1]}">{ext(x.name) || 'file'}</span>
                <span style="display:flex;flex-direction:column;gap:3px;min-width:0">
                  <span class="nm">{x.name}</span>
                  <span class="note ell">{results ? x.path.split('/').slice(0, -1).join('/') : `v${x.version}${x.project ? ' · ' + x.project : ''}`}</span>
                </span>
              </span>
              <span class="ell" style="color:var(--text-2)">{x.saved_by || '—'}</span>
              <span class="row" style="gap:8px;color:var(--text-2);white-space:nowrap"><span class="dot" style="width:6px;height:6px;background:{s[1]}"></span>{s[0]}</span>
              <span class="mono" style="text-align:right;color:var(--text-2);font-size:12px">{bytes(x.size)}</span>
              <span style="text-align:right;color:var(--muted);font-size:12px;white-space:nowrap">{ago(x.updated_at)}</span>
            </button>
          {/each}
          {#if !folders.length && !rows.length}
            <div class="empty">
              {#if results}No files match “{q}” here.
              {:else if canEdit}This folder is empty. Drop files anywhere on the page, or ask Claude Code to save here:<br /><code>save this to {path}</code>
              {:else}This folder is empty.{/if}
            </div>
          {/if}
        </div></div>
      {/if}
    </section>

    {#if sel}
      {@const t = extTint(sel.name)}
      <aside aria-label="File details" class="card detail">
        <a class="pv" href={`/view?path=${encodeURIComponent(sel.path)}`} aria-label="Open {sel.name}">
          {#if isFrame(sel)}
            <iframe title="Preview of {sel.name}" src={sel.preview_url} sandbox="allow-scripts" referrerpolicy="no-referrer"></iframe>
          {:else if isImage(sel)}
            <img alt={sel.name} src={sel.preview_url} />
          {:else if text !== null && /\.(md|markdown)$/i.test(sel.name)}
            <div class="mdmini">{@html markdown(text.slice(0, 4000))}</div>
          {:else if text !== null}
            <pre class="txt">{text.slice(0, 6000)}</pre>
          {:else}
            <div class="ph"><span class="mono" style="font-size:26px;font-weight:500;letter-spacing:.04em;color:{t[1]}">.{ext(sel.name) || 'file'}</span><span class="note">No preview for this type</span></div>
          {/if}
        </a>
        <div style="display:flex;flex-direction:column;gap:6px">
          <a href={`/view?path=${encodeURIComponent(sel.path)}`} class="dname">{sel.name}</a>
          <span class="note">{bytes(sel.size)} · version {sel.version} · {when(sel.updated_at)}</span>
        </div>
        <div class="row" style="gap:8px">
          <a class="btn pri grow" href={`/view?path=${encodeURIComponent(sel.path)}`}><Icon name="eye" />Open</a>
          <a class="btn" data-native href={api.download(sel.path)} download aria-label="Download" title="Download"><Icon name="download" /></a>
        </div>
        <div class="acts">
          <button class="btn sm" onclick={() => (dialog = { kind: 'versions', path: sel.path })}><Icon name="history" size={14} />Versions</button>
          {#if canEdit}
            <button class="btn sm" onclick={() => { nameInput = sel.name; dialog = { kind: 'rename', item: sel }; }}><Icon name="edit" size={14} />Rename</button>
            <button class="btn sm" onclick={() => { destInput = path; dialog = { kind: 'move', item: sel }; }}><Icon name="move" size={14} />Move</button>
            <button class="btn sm danger" onclick={() => (dialog = { kind: 'delete', item: sel })}><Icon name="trash" size={14} />Delete</button>
          {/if}
        </div>
        <div>
          <div class="kv"><span>Folder</span><span style="text-align:right">{crumbs(sel.path.split('/').slice(0, -1).join('/')).filter((c) => c.path !== '/users' && c.path !== '/shared').map((c) => (c.path === home ? 'Home' : c.name)).join(' / ')}</span></div>
          <div class="kv"><span>Saved by</span><span>{sel.saved_by || '—'}</span></div>
          <div class="kv"><span>From</span><span>{(SOURCE[sel.source] || [sel.source])[0]}</span></div>
          {#if sel.project}<div class="kv"><span>Project</span><span class="mono">{sel.project}</span></div>{/if}
          {#if sel.git_remote}<div class="kv"><span>Repository</span><span class="mono" style="word-break:break-all">{sel.git_remote}</span></div>{/if}
          {#if sel.git_commit}<div class="kv"><span>Commit</span><span class="mono">{sel.git_commit.slice(0, 10)}</span></div>{/if}
          {#if sel.tags?.length}<div class="kv"><span>Tags</span><span class="row wrap" style="gap:4px">{#each sel.tags as tg}<span class="tag">{tg}</span>{/each}</span></div>{/if}
        </div>
        <button class="btn" onclick={() => (dialog = { kind: 'share', path: sel.path.split('/').slice(0, -1).join('/') })}><Icon name="users" />Who can open this</button>
      </aside>
    {/if}
  </div>
</div>

{#if dialog?.kind === 'share'}
  <ShareDialog path={dialog.path} canShare={canShare} onclose={() => (dialog = null)} onchange={loadTree} />
{:else if dialog?.kind === 'versions'}
  <Versions path={dialog.path} {canEdit} onclose={() => (dialog = null)} onchange={() => { load(); rev++; }} />
{:else if dialog?.kind === 'mkdir'}
  <Modal title="New folder in {path.split('/').pop()}" onclose={() => (dialog = null)}>
    <form onsubmit={(e) => { e.preventDefault(); newFolder(nameInput.trim()); }} style="display:flex;flex-direction:column;gap:14px">
      <label class="lbl">Name<input class="field" bind:value={nameInput} required maxlength="200" /></label>
      <div class="row" style="justify-content:flex-end"><button type="button" class="btn" onclick={() => (dialog = null)}>Cancel</button><button class="btn pri">Create</button></div>
    </form>
  </Modal>
{:else if dialog?.kind === 'rename'}
  <Modal title="Rename" onclose={() => (dialog = null)}>
    <form onsubmit={(e) => { e.preventDefault(); rename(dialog.item, nameInput.trim()); }} style="display:flex;flex-direction:column;gap:14px">
      <label class="lbl">New name<input class="field" bind:value={nameInput} required /></label>
      <div class="row" style="justify-content:flex-end"><button type="button" class="btn" onclick={() => (dialog = null)}>Cancel</button><button class="btn pri">Rename</button></div>
    </form>
  </Modal>
{:else if dialog?.kind === 'move'}
  <Modal title="Move {dialog.item.name}" onclose={() => (dialog = null)}>
    <form onsubmit={(e) => { e.preventDefault(); move(dialog.item, destInput.trim()); }} style="display:flex;flex-direction:column;gap:14px">
      <label class="lbl">To folder<input class="field mono" bind:value={destInput} required list="move-dests" /></label>
      <datalist id="move-dests">
        <option value={home}></option><option value={home + '/inbox'}></option><option value={home + '/projects'}></option>
        {#each shared as f}<option value={f.path}></option><option value={f.path + '/projects'}></option>{/each}
      </datalist>
      <p class="note" style="margin:0">You need Editor on the folder it goes to. Sharing set on the destination applies from then on.</p>
      <div class="row" style="justify-content:flex-end"><button type="button" class="btn" onclick={() => (dialog = null)}>Cancel</button><button class="btn pri">Move</button></div>
    </form>
  </Modal>
{:else if dialog?.kind === 'delete'}
  <Modal title="Move {dialog.item.name} to trash?" onclose={() => (dialog = null)}>
    <p style="margin:0;font-size:13px;line-height:1.55;color:var(--text-2)">{#if dialog.item.type === 'folder'}{dialog.files ? `The folder and the ${dialog.files} ${dialog.files === 1 ? 'file' : 'files'} in it (${bytes(dialog.bytes)}) stay` : 'The empty folder stays'}{:else}It stays{/if} in your trash for {session.trash_days || 30} days, with versions and sharing, and you can restore it from there.</p>
    <div class="row" style="justify-content:flex-end"><button class="btn" onclick={() => (dialog = null)}>Cancel</button><button class="btn pri" onclick={() => remove(dialog.item)}>Move to trash</button></div>
  </Modal>
{/if}

<style>
  .fm { position: relative; }
  .menu { position: absolute; right: 0; top: calc(100% + 6px); z-index: 20; padding: 6px; min-width: 180px; display: flex; flex-direction: column; box-shadow: 0 16px 40px rgba(0,0,0,.45); }
  .menu button { all: unset; box-sizing: border-box; display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 7px; font-size: 13px; color: var(--text-2); cursor: pointer; }
  .menu button:hover, .menu button:focus-visible { background: var(--raise); color: var(--text); }
  .menu button.danger { color: var(--red); }
  .crumbs { display: flex; align-items: center; gap: 8px; font-size: 14px; flex-wrap: wrap; }
  .crumbs a:hover { color: var(--text); }
  .here { font-size: 22px; font-weight: 600; letter-spacing: -0.02em; }
  .search { position: relative; display: block; width: 260px; }
  .dropzone { border: 1.5px dashed var(--amber-edge); background: #17130C; border-radius: 14px; padding: 22px 26px; display: flex; align-items: center; gap: 18px; flex-wrap: wrap; }
  .dropzone.hot { border-color: var(--amber); background: #1D170D; }
  .dzicon { width: 44px; height: 44px; border-radius: 12px; background: var(--amber-bg); color: var(--amber); display: grid; place-items: center; }
  .layout { display: flex; flex-wrap: wrap; gap: 16px; align-items: flex-start; }
  .tree { flex: 0 0 210px; padding: 14px 10px; display: flex; flex-direction: column; gap: 2px; position: sticky; top: 20px; }
  .leaf { display: flex; align-items: center; gap: 10px; height: 32px; padding: 0 10px; border-radius: 7px; font-size: 13px; color: var(--text-2); }
  .leaf :global(.ic) { color: var(--muted); }
  .leaf:hover { background: #17191C; color: var(--text); }
  .leaf.on { background: var(--raise); color: var(--text); }
  .leaf.on :global(.ic) { color: var(--amber); }
  .listing { flex: 999 1 480px; min-width: 0; overflow: hidden; transition: border-color .12s; }
  .listing.hot { border-color: var(--amber); }
  .rowbtn { all: unset; box-sizing: border-box; width: 100%; padding: 0 18px; height: 56px; cursor: pointer; border-bottom: 1px solid var(--line-soft); font-size: 13px; }
  .grid { display: grid; grid-template-columns: minmax(0, 2.6fr) minmax(0, 1.3fr) minmax(0, 1fr) 72px 96px; gap: 16px; align-items: center; }
  .head { padding: 14px 18px; border-bottom: 1px solid var(--line); }
  .listing { container-type: inline-size; }
  @container (max-width: 700px) {
    .grid { grid-template-columns: minmax(0, 2.6fr) minmax(0, 1.2fr) 64px 80px; }
    .grid > :nth-child(3) { display: none; }
  }
  .detail .tag { white-space: normal; max-width: 100%; overflow-wrap: anywhere; }
  .acts { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
  .rowbtn:hover { background: var(--card-2); }
  .rowbtn.on { background: #1A1814; box-shadow: inset 2px 0 0 var(--amber); }
  .fi { flex: none; width: 40px; height: 28px; border-radius: 6px; display: grid; place-items: center; background: var(--amber-bg); color: var(--amber); }
  .ext { flex: none; width: 40px; height: 28px; border-radius: 6px; display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 500; letter-spacing: .04em; text-transform: uppercase; }
  .nm { font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .ell { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .detail { flex: 1 1 300px; max-width: 360px; min-width: 0; padding: 18px; display: flex; flex-direction: column; gap: 16px; position: sticky; top: 20px; }
  .pv { height: 200px; border-radius: 10px; border: 1px solid var(--line); overflow: hidden; background-color: #0F1012;
    background-image: linear-gradient(#17191C 1px, transparent 1px), linear-gradient(90deg, #17191C 1px, transparent 1px); background-size: 16px 16px; }
  .pv { display: block; position: relative; }
  .pv iframe { width: 100%; height: 100%; border: 0; background: #fff; pointer-events: none; }
  .pv:hover { border-color: var(--line-hover); }
  .pv:focus-visible { outline: 2px solid var(--amber); outline-offset: 2px; }
  .dname { font-size: 16px; font-weight: 600; letter-spacing: -0.01em; word-break: break-all; color: var(--text); }
  .dname:hover { color: var(--amber); }
  .pv img { width: 100%; height: 100%; object-fit: contain; }
  .ph { height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; }
  .mdmini { height: 100%; overflow: hidden; padding: 14px 16px; background: #F7F5F0; color: #2A2C31; font-size: 10.5px; line-height: 1.5;
    mask-image: linear-gradient(#000 70%, transparent); -webkit-mask-image: linear-gradient(#000 70%, transparent); }
  .mdmini :global(h1) { font-size: 15px; margin: 0 0 6px; } .mdmini :global(h2) { font-size: 12.5px; margin: 10px 0 4px; } .mdmini :global(h3) { font-size: 11px; margin: 8px 0 3px; }
  .mdmini :global(p), .mdmini :global(ul), .mdmini :global(ol), .mdmini :global(table) { margin: 0 0 6px; } .mdmini :global(ul), .mdmini :global(ol) { padding-left: 16px; }
  .mdmini :global(code) { font-size: 9.5px; background: #EAE6DC; color: #3A3428; border: 0; padding: 0 3px; }
  .mdmini :global(table) { border-collapse: collapse; } .mdmini :global(td), .mdmini :global(th) { border: 1px solid #DDD8CC; padding: 2px 5px; }
  .mdmini :global(a) { color: #A8661A; }
  .txt { margin: 0; padding: 12px 14px; font: 11.5px/1.6 var(--mono); color: var(--text-2); white-space: pre-wrap; word-break: break-word; height: 100%; overflow: auto; background: #0F1012; }
  @media (max-width: 1100px) { .tree { position: static; flex: 1 1 100%; flex-direction: row; flex-wrap: wrap; } .tree .sec { display: none; } .detail { position: static; max-width: none; } }
</style>
