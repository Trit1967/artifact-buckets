<script>
  // Storage on protected disks (ZFS behind the scenes). Admins see health, disks, restore points and backups;
  // every hard part runs on its own. Nothing here uses ZFS words.
  import { api } from '../lib/api.js';
  import { session } from '../lib/session.svelte.js';
  import { toast, fail } from '../lib/toast.svelte.js';
  import { bytes, ago, when } from '../lib/format.js';
  import Icon from '../lib/Icon.svelte';
  import Modal from '../lib/Modal.svelte';

  let st = $state(null);
  let points = $state([]);
  let err = $state('');
  let dialog = $state(null);
  let busy = $state(false);

  async function load() {
    try {
      st = await api.get('/api/storage/protected');
      points = (await api.get('/api/storage/protected/points')).points;
      err = '';
    } catch (e) { err = e.message; }
  }
  load();
  const t = setInterval(() => { if (st?.scrub?.running || st?.vdevs?.some((v) => v.health !== 'ONLINE')) load(); }, 8000);
  $effect(() => () => clearInterval(t));

  const disks = $derived(st ? st.vdevs.flatMap((v) => v.disks.map((d) => ({ ...d, group: v }))) : []);
  const singles = $derived(st ? st.vdevs.filter((v) => v.type === 'single') : []);
  const usedPct = $derived(st && st.size ? Math.min(100, (st.alloc / st.size) * 100) : 0);
  const KIND = { hourly: 'Hourly', daily: 'Daily', weekly: 'Weekly', monthly: 'Monthly', manual: 'Made by hand' };
  const DISK = { ONLINE: ['Healthy', 'green'], DEGRADED: ['Degraded', 'amber'], FAULTED: ['Failed', 'red'], UNAVAIL: ['Missing', 'red'], OFFLINE: ['Offline', ''], REMOVED: ['Removed', 'red'] };
  const diskName = (d) => d.file ? 'Space on the system disk' : (d.name || d.id).replace(/^scsi-0QEMU_HARDDISK_|^ata-|^nvme-/, '');

  async function act(fn, ok) {
    busy = true;
    try { await fn(); toast(ok); dialog = null; await load(); } catch (e) { fail(e); } finally { busy = false; }
  }
  const point = () => act(() => api.post('/api/storage/protected/point'), 'Restore point made');
  const check = () => act(() => api.post('/api/storage/protected/health-check'), 'Health check started. It runs in the background.');
  const backupNow = () => act(async () => { const b = await api.post('/api/storage/protected/backup'); if (b.last_error) throw new Error(b.last_error); }, 'Backed up');

  // dialogs
  function openAdd() { dialog = { kind: 'add', pick: [], layout: st.free_disks.length >= 2 ? 'mirror' : 'single' }; }
  function openMirror(d) { dialog = { kind: 'mirror', disk: d, pick: st.free_disks[0]?.id || '' }; }
  function openReplace(d) { dialog = { kind: 'replace', disk: d, pick: st.free_disks[0]?.id || '' }; }
  function openGrow(d) { dialog = { kind: 'grow', disk: d, gb: 10 }; }
  function openKeep() { dialog = { kind: 'keep', keep: { ...st.keep } }; }
  function openBackup() { const b = st.backup || {}; dialog = { kind: 'backup', where: b.kind || 'ssh', host: b.host || '', dataset: b.dataset || 'backup/buckets' }; }
  function togglePick(id) { dialog.pick = dialog.pick.includes(id) ? dialog.pick.filter((x) => x !== id) : [...dialog.pick, id].slice(-2); }

  // restore browser
  async function browse(p, path = '/') {
    try {
      const r = await api.get('/api/storage/protected/browse', { point: p.name, path });
      dialog = { kind: 'browse', point: p, path: r.path, items: r.items };
    } catch (e) { fail(e); }
  }
  function into(it) { browse(dialog.point, (dialog.path === '/' ? '' : dialog.path) + '/' + it.name); }
  function up() { const p = dialog.path.split('/').slice(0, -1).join('/') || '/'; browse(dialog.point, p); }
  function restore(it) {
    const path = (dialog.path === '/' ? '' : dialog.path) + '/' + it.name;
    act(async () => {
      const r = await api.post('/api/storage/protected/restore', { point: dialog.point.name, path });
      toast(it.folder ? `Restored ${r.files} ${r.files === 1 ? 'file' : 'files'} to ${r.restored_to.split('/').pop()}` : `${it.name} is back as it was, as a new version`);
    }, 'Done');
  }
  const canRestoreHere = $derived(dialog?.kind === 'browse' && dialog.path.split('/').filter(Boolean).length >= 2);
</script>

{#if err}
  <section class="card pad"><h2>Protected storage</h2><p class="note" style="margin:8px 0 0">{err}</p></section>
{:else if !st}
  <section class="card pad empty">Checking the disks…</section>
{:else}
  <section class="card hero">
    <div class="row wrap" style="justify-content:space-between;gap:18px;align-items:flex-start">
      <div style="display:flex;gap:16px;align-items:center">
        <span class="badge" class:bad={!st.summary.healthy}><Icon name={st.summary.healthy ? 'shield' : 'alert'} size={22} /></span>
        <div style="display:flex;flex-direction:column;gap:4px">
          <span class="big">{st.summary.healthy ? 'Healthy' : 'Needs attention'}</span>
          <span class="muted" style="font-size:13px">
            {st.summary.disks} {st.summary.disks === 1 ? 'disk' : 'disks'}{st.summary.mirrored && st.summary.disks > 1 ? ', each file kept on two' : ''}
            · restore points {points.length ? `back to ${when(points[points.length - 1].created)}` : 'start within the hour'}
            · {st.backup ? (st.backup.last_error ? 'last backup failed' : st.backup.last_ok ? `backed up ${ago(st.backup.last_ok)} ago` : 'backup set up') : 'no off-site backup'}
          </span>
        </div>
      </div>
      {#if session.isAdmin}
        <div class="row" style="gap:8px">
          <button class="btn" onclick={point} disabled={busy}><Icon name="history" />Make a restore point</button>
          <button class="btn pri" onclick={openAdd} disabled={!st.free_disks.length} title={st.free_disks.length ? '' : 'Attach a new disk to the server first'}><Icon name="plus" />Add disk</button>
        </div>
      {/if}
    </div>
    <div style="display:flex;flex-direction:column;gap:8px">
      <div class="row" style="justify-content:space-between;font-size:13px"><span><b class="mono">{bytes(st.alloc)}</b> <span class="muted">used of {bytes(st.size, 0)}</span></span><span class="muted">Files take {st.compression === '1.00x' ? 'their full size' : `about ${Math.round(100 / parseFloat(st.compression))}% of their size`} on disk</span></div>
      <div class="bar"><div style="width:{usedPct}%" class:warn={usedPct > 80}></div></div>
    </div>
    {#if st.summary.problems.length}
      <div class="alert"><Icon name="alert" /><span>{#each st.summary.problems as p, i}{i ? '; ' : ''}<b>{diskName(p)}</b> is {(DISK[p.health] || [p.health])[0].toLowerCase()}{p.errors ? ` with ${p.errors} errors` : ''}{/each}. Files are still readable{st.summary.mirrored ? ' from the other copy' : ''}. Replace the disk below.</span></div>
    {/if}
  </section>

  <section class="duo">
    <div class="card pad">
      <h2>Disks</h2>
      <ul class="disks">
        {#each disks as d}
          {@const h = DISK[d.health] || [d.health, '']}
          <li>
            <span class="dicon" class:bad={d.health !== 'ONLINE'}><Icon name={d.file ? 'doc' : 'db'} size={16} /></span>
            <span style="display:flex;flex-direction:column;gap:2px;min-width:0;flex:1">
              <span style="font-weight:500;font-size:13.5px" class="ell" title={d.id}>{diskName(d)}</span>
              <span class="note">{bytes(d.size, 0)}{d.group.type === 'mirror' ? ' · mirrored pair' : ''}{d.read + d.write + d.cksum ? ` · ${d.read + d.write + d.cksum} errors` : ''}</span>
            </span>
            <span class="tag {h[1]}">{h[0]}</span>
            {#if session.isAdmin}
              <span class="row" style="gap:6px">
                {#if d.health !== 'ONLINE' || d.read + d.write + d.cksum}<button class="btn sm pri" onclick={() => openReplace(d)} disabled={!st.free_disks.length}>Replace</button>
                {:else if d.group.type === 'single' && st.free_disks.length}<button class="btn sm" onclick={() => openMirror(d)} title="Keep a second copy of everything on another disk">Add a copy</button>{/if}
                {#if d.file}<button class="btn sm" onclick={() => openGrow(d)}>Grow</button>{/if}
              </span>
            {/if}
          </li>
        {/each}
      </ul>
      {#if st.free_disks.length}
        <p class="note" style="margin:12px 0 0">{st.free_disks.length} new {st.free_disks.length === 1 ? 'disk is' : 'disks are'} attached and ready to use.</p>
      {:else}
        <p class="note" style="margin:12px 0 0">To add space, attach a disk to the server; it appears here, ready to add. {#if st.summary.file_backed}Or grow the space on the system disk.{/if}</p>
      {/if}
      <div class="row" style="justify-content:space-between;margin-top:16px;padding-top:14px;border-top:1px solid var(--line-soft)">
        <span style="display:flex;flex-direction:column;gap:2px"><span style="font-size:13px;font-weight:500">Health check</span><span class="note">{st.scrub.running ? 'Running now…' : 'Reads every file and repairs any damage. Runs on its own once a month.'}</span></span>
        {#if session.isAdmin}<button class="btn sm" onclick={check} disabled={busy || st.scrub.running}>Run now</button>{/if}
      </div>
    </div>

    <div class="card pad">
      <div class="row" style="justify-content:space-between"><h2>Restore points</h2>{#if session.isAdmin}<button class="lnk" onclick={openKeep}>Schedule</button>{/if}</div>
      <p class="note" style="margin:6px 0 12px">The whole tree as it was, taken automatically. Bring back any file or folder from one. People on Windows see them as <b>Previous Versions</b>.</p>
      <ul class="points">
        {#each points.slice(0, 8) as p}
          <li>
            <span class="pdot {p.kind}"></span>
            <span style="display:flex;flex-direction:column;gap:2px;flex:1;min-width:0"><span style="font-size:13px">{when(p.created)}</span><span class="note">{KIND[p.kind]}</span></span>
            {#if session.isAdmin}<button class="btn sm" onclick={() => browse(p)}>Browse</button>{/if}
          </li>
        {:else}<li class="note">The first one is made within the hour.</li>{/each}
      </ul>
      {#if points.length > 8}<p class="note" style="margin:8px 0 0">{points.length - 8} older ones, back to {when(points[points.length - 1].created)}.</p>{/if}
    </div>
  </section>

  <section class="duo">
    <div class="card pad">
      <div class="row" style="justify-content:space-between"><h2>Off-site backup</h2>{#if session.isAdmin}<button class="lnk" onclick={openBackup}>{st.backup ? 'Change' : 'Set up'}</button>{/if}</div>
      {#if st.backup}
        <div class="kv" style="margin-top:12px"><span>Goes to</span><span class="mono">{st.backup.host ? st.backup.host + ':' : 'this server, '}{st.backup.dataset}</span></div>
        <div class="kv"><span>Last good backup</span><span>{st.backup.last_ok ? when(st.backup.last_ok) : 'Not yet'}</span></div>
        {#if st.backup.last_error}<div class="alert" style="margin-top:10px"><Icon name="alert" /><span>{st.backup.last_error}</span></div>{/if}
        <p class="note" style="margin:10px 0 12px">Runs every night after 1 AM and sends only what changed.</p>
        {#if session.isAdmin}<button class="btn sm" onclick={backupNow} disabled={busy}>Back up now</button>{/if}
      {:else}
        <p class="note" style="margin:10px 0 0">Restore points live on the same disks. For a copy somewhere else, point this at another server with ZFS (a NAS or a second host). It then runs every night by itself.</p>
      {/if}
    </div>
    <div class="card pad">
      <h2>Recent</h2>
      <ul class="events">
        {#each (st.events || []).slice(0, 7) as e}
          <li><Icon name={e.ok ? 'check' : 'alert'} size={14} style="color:{e.ok ? 'var(--green)' : 'var(--red)'};flex:none;margin-top:2px" /><span style="flex:1">{e.text}</span><span class="note" style="white-space:nowrap">{ago(e.ts)}</span></li>
        {:else}<li class="note">Nothing yet.</li>{/each}
      </ul>
    </div>
  </section>
{/if}

{#if dialog?.kind === 'add'}
  <Modal title="Add disk" onclose={() => (dialog = null)}>
    <p class="note" style="margin:0">Pick two for a mirrored pair (each file kept on both, so either disk can fail), or one for plain extra space.</p>
    <div style="display:flex;flex-direction:column;gap:8px">
      {#each st.free_disks as d}
        <label class="pick" class:on={dialog.pick.includes(d.id)}><input type="checkbox" checked={dialog.pick.includes(d.id)} onchange={() => togglePick(d.id)} /><span style="flex:1"><b style="font-weight:500">{diskName(d)}</b> <span class="note">{d.model} · {d.kind.toUpperCase()}</span></span><span class="mono">{bytes(d.size, 0)}</span></label>
      {/each}
    </div>
    <div class="row" style="justify-content:flex-end"><button class="btn" onclick={() => (dialog = null)}>Cancel</button>
      <button class="btn pri" disabled={busy || !dialog.pick.length} onclick={() => act(() => api.post('/api/storage/protected/disks/add', { disks: dialog.pick, layout: dialog.pick.length === 2 ? 'mirror' : 'single' }), 'Disk added. The space is available now.')}>{dialog.pick.length === 2 ? 'Add as mirrored pair' : 'Add disk'}</button></div>
  </Modal>
{:else if dialog?.kind === 'mirror' || dialog?.kind === 'replace'}
  <Modal title={dialog.kind === 'mirror' ? `Keep a copy of ${diskName(dialog.disk)}` : `Replace ${diskName(dialog.disk)}`} onclose={() => (dialog = null)}>
    <p class="note" style="margin:0">{dialog.kind === 'mirror' ? 'Everything on it is copied to the new disk in the background, and kept on both from then on.' : 'Everything is copied onto the new disk in the background. People keep working; when it finishes the old disk can be removed.'}</p>
    <label class="lbl">New disk<select class="field" bind:value={dialog.pick}>{#each st.free_disks as d}<option value={d.id}>{diskName(d)} ({bytes(d.size, 0)})</option>{/each}</select></label>
    <div class="row" style="justify-content:flex-end"><button class="btn" onclick={() => (dialog = null)}>Cancel</button>
      <button class="btn pri" disabled={busy || !dialog.pick} onclick={() => dialog.kind === 'mirror'
        ? act(() => api.post('/api/storage/protected/disks/mirror', { existing: dialog.disk.id, new: dialog.pick }), 'Copying in the background')
        : act(() => api.post('/api/storage/protected/disks/replace', { old: dialog.disk.id, new: dialog.pick }), 'Replacing in the background')}>{dialog.kind === 'mirror' ? 'Add copy' : 'Replace'}</button></div>
  </Modal>
{:else if dialog?.kind === 'grow'}
  <Modal title="Grow space on the system disk" onclose={() => (dialog = null)}>
    <p class="note" style="margin:0">Adds room straight away. It only takes real disk space as files arrive, so keep an eye on the system disk.</p>
    <label class="lbl">Add (GB)<input class="field" type="number" min="1" max="1024" step="1" bind:value={dialog.gb} /></label>
    <div class="row" style="justify-content:flex-end"><button class="btn" onclick={() => (dialog = null)}>Cancel</button>
      <button class="btn pri" disabled={busy} onclick={() => act(() => api.post('/api/storage/protected/disks/grow', { disk: dialog.disk.id, gigabytes: +dialog.gb }), `Added ${dialog.gb} GB`)}>Grow</button></div>
  </Modal>
{:else if dialog?.kind === 'keep'}
  <Modal title="Restore point schedule" onclose={() => (dialog = null)}>
    <p class="note" style="margin:0">How many of each to keep. Older ones are removed on their own. Restore points take space only for what changed since.</p>
    <div class="grid4">
      {#each ['hourly', 'daily', 'weekly', 'monthly'] as k}<label class="lbl">{KIND[k]}<input class="field" type="number" min="0" max="400" bind:value={dialog.keep[k]} /></label>{/each}
    </div>
    <div class="row" style="justify-content:flex-end"><button class="btn" onclick={() => (dialog = null)}>Cancel</button>
      <button class="btn pri" disabled={busy} onclick={() => act(() => api.put('/api/storage/protected/schedule', Object.fromEntries(Object.entries(dialog.keep).map(([k, v]) => [k, +v]))), 'Schedule saved')}>Save</button></div>
  </Modal>
{:else if dialog?.kind === 'backup'}
  <Modal title="Off-site backup" onclose={() => (dialog = null)}>
    <div class="segs" role="group" aria-label="Where"><button class="seg" class:on={dialog.where === 'ssh'} onclick={() => (dialog.where = 'ssh')}>Another server</button><button class="seg" class:on={dialog.where === 'local'} onclick={() => (dialog.where = 'local')}>A disk on this server</button></div>
    {#if dialog.where === 'ssh'}<label class="lbl">Server (user@host)<input class="field mono" bind:value={dialog.host} placeholder="root@nas.example.com" /></label>{/if}
    <label class="lbl">Where on it<input class="field mono" bind:value={dialog.dataset} placeholder="backup/buckets" /></label>
    {#if dialog.where === 'ssh'}<p class="note" style="margin:0">The server needs ZFS and must accept this server's SSH key for that user.</p>{/if}
    <div class="row" style="justify-content:space-between">
      {#if st.backup}<button class="btn danger" onclick={() => act(() => api.put('/api/storage/protected/backup', {}), 'Backups turned off')}>Turn off</button>{:else}<span></span>{/if}
      <span class="row" style="gap:8px"><button class="btn" onclick={() => (dialog = null)}>Cancel</button>
      <button class="btn pri" disabled={busy || !dialog.dataset} onclick={() => act(() => api.put('/api/storage/protected/backup', { kind: dialog.where, host: dialog.where === 'ssh' ? dialog.host : null, dataset: dialog.dataset }), 'Backups set up. The first runs tonight.')}>Save</button></span>
    </div>
  </Modal>
{:else if dialog?.kind === 'browse'}
  <Modal title="As it was {when(dialog.point.created)}" onclose={() => (dialog = null)} width={640}>
    <div class="row" style="gap:8px;font-size:13px">
      <button class="btn sm icon" onclick={up} disabled={dialog.path === '/'} aria-label="Up a folder"><Icon name="back" size={14} /></button>
      <span class="mono ell">{dialog.path}</span>
    </div>
    <ul class="browse">
      {#each dialog.items as it}
        <li>
          <button class="rowbtn" onclick={() => it.folder ? into(it) : null} disabled={!it.folder}><Icon name={it.folder ? 'folder' : 'doc'} size={15} /><span class="ell">{it.name}</span></button>
          <span class="note mono">{it.folder ? '' : bytes(it.size)}</span>
          {#if canRestoreHere}<button class="btn sm" disabled={busy} onclick={() => restore(it)}>{it.folder ? 'Restore copy' : 'Restore'}</button>{/if}
        </li>
      {:else}<li class="note">Empty.</li>{/each}
    </ul>
    <p class="note" style="margin:0">A file comes back as a new version, so the current one stays in its history. A folder comes back beside the current one, named "(restored …)".</p>
  </Modal>
{/if}

<style>
  .pad { padding: 22px 24px; }
  .hero { padding: 22px 24px; display: flex; flex-direction: column; gap: 18px; }
  .big { font-size: 24px; font-weight: 600; letter-spacing: -0.02em; }
  .badge { width: 46px; height: 46px; border-radius: 12px; display: grid; place-items: center; background: #12261C; color: var(--green); border: 1px solid #1F4030; flex: none; }
  .badge.bad { background: var(--red-bg); color: var(--red); border-color: #5A2E24; }
  .bar { height: 12px; border-radius: 6px; background: #1E2125; overflow: hidden; }
  .bar > div { height: 100%; background: var(--amber); border-radius: 6px; }
  .bar > div.warn { background: var(--red); }
  .alert { display: flex; gap: 10px; align-items: flex-start; padding: 12px 14px; border-radius: 10px; background: var(--red-bg); border: 1px solid #5A2E24; color: var(--text-2); font-size: 13px; line-height: 1.5; }
  .duo { display: flex; flex-wrap: wrap; gap: 16px; }
  .duo > * { flex: 1 1 360px; min-width: 0; }
  .disks, .points, .events, .browse { list-style: none; margin: 14px 0 0; padding: 0; display: flex; flex-direction: column; }
  .disks li { display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--line-soft); }
  .disks li:last-child { border-bottom: 0; }
  .dicon { width: 32px; height: 32px; border-radius: 8px; display: grid; place-items: center; background: var(--card-2); border: 1px solid var(--line); color: var(--text-2); flex: none; }
  .dicon.bad { color: var(--red); border-color: #5A2E24; }
  .points li { display: flex; align-items: center; gap: 12px; padding: 8px 0; }
  .pdot { width: 8px; height: 8px; border-radius: 50%; background: #4A4F57; flex: none; }
  .pdot.daily { background: var(--amber); } .pdot.weekly, .pdot.monthly { background: #8AA4FF; } .pdot.manual { background: var(--green); }
  .events li { display: flex; gap: 10px; padding: 7px 0; font-size: 13px; color: var(--text-2); }
  .browse { margin: 0; max-height: 50vh; overflow: auto; border: 1px solid var(--line); border-radius: 10px; }
  .browse li { display: flex; align-items: center; gap: 10px; padding: 6px 10px; border-bottom: 1px solid var(--line-soft); }
  .browse li:last-child { border-bottom: 0; }
  .browse .rowbtn { all: unset; display: flex; gap: 8px; align-items: center; flex: 1; min-width: 0; cursor: pointer; font-size: 13px; padding: 4px 0; }
  .browse .rowbtn:disabled { cursor: default; }
  .browse .rowbtn:focus-visible { outline: 2px solid var(--amber); outline-offset: 2px; border-radius: 4px; }
  .pick { display: flex; gap: 12px; align-items: center; padding: 10px 12px; border-radius: 10px; border: 1px solid var(--line); cursor: pointer; font-size: 13px; }
  .pick.on { border-color: var(--amber-edge); background: #17130C; }
  .pick input { accent-color: var(--amber); }
  .grid4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
  @media (max-width: 520px) { .grid4 { grid-template-columns: 1fr 1fr; } }
  .ell { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .lnk { all: unset; cursor: pointer; color: var(--amber); font-size: 13px; }
  .lnk:focus-visible { outline: 2px solid var(--amber); outline-offset: 2px; border-radius: 3px; }
  .kv { display: flex; justify-content: space-between; gap: 12px; padding: 7px 0; font-size: 13px; border-bottom: 1px solid var(--line-soft); }
  .kv > span:first-child { color: var(--text-3, #8A919A); }
</style>
