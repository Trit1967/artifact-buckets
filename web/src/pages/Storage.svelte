<script>
  import { api } from '../lib/api.js';
  import { session } from '../lib/session.svelte.js';
  import { toast, fail } from '../lib/toast.svelte.js';
  import { bytes, ago } from '../lib/format.js';
  import Icon from '../lib/Icon.svelte';
  import Copy from '../lib/Copy.svelte';
  import Modal from '../lib/Modal.svelte';
  import ProtectedStorage from './ProtectedStorage.svelte';

  let pool = $state(null);
  let quotas = $state(null);
  let qf = $state({ user_gb: 50, group_gb: 500 });
  let confirm = $state(null);
  const COLORS = ['#F2A93B', '#8AA4FF', '#4CC38A', '#D59BE6', '#5CC8D0'];

  async function load() {
    try {
      pool = await api.get('/api/storage');
      quotas = await api.get('/api/storage/quotas');
      qf = { user_gb: +(quotas.user_bytes / 1024 ** 3).toFixed(1), group_gb: +(quotas.group_bytes / 1024 ** 3).toFixed(1) };
    } catch (e) { fail(e); }
  }
  load();
  const t = setInterval(() => { if (pool?.volumes.some((v) => v.status === 'draining')) load(); }, 5000);
  $effect(() => () => clearInterval(t));

  async function act(v, action) {
    try {
      await api.post(`/api/storage/${v.id}/${action}`);
      toast({ add: `${v.name} is in the pool. New files can land on it now.`, drain: `Draining ${v.name}. Files move in the background.`, undrain: `${v.name} takes new files again.` }[action]);
      confirm = null; load();
    } catch (e) { fail(e); }
  }
  async function policy(p) { try { pool = await api.put('/api/storage/policy', { policy: p }); toast('Saved'); } catch (e) { fail(e); } }
  async function saveQuotas(e) { e.preventDefault(); try { await api.put('/api/storage/quotas', qf); toast('Default quotas saved'); load(); } catch (e) { fail(e); } }

  const plain = $derived(pool?.mode === 'direct');
  const live = $derived((pool?.volumes || []).filter((v) => ['active', 'draining'].includes(v.status)));
  const STATUS = { pending: ['Waiting to join', 'blue'], active: ['In pool', 'green'], draining: ['Draining', 'amber'], drained: ['Drained, safe to unmount', ''], missing: ['Not mounted', 'red'] };
  const CHECK = { mounted: 'Mounted', readable: 'Readable', writable: 'Writable' };
</script>

<div class="page">
  <header class="page-head">
    {#if session.protected_storage}
      <div><h1>Storage</h1><p class="sub">Disks, restore points and backups. Health checks, restore points and backups run on their own; this page is for when you want to look or change something.</p></div>
    {:else}
      {#if plain}
      <div><h1>Storage</h1><p class="sub">Files are kept in plain folders on this server's disk, which the system and Docker share. Quotas below limit what each person and team folder can use.</p></div>
      {:else}
      <div><h1>Storage</h1><p class="sub">Every volume mounted under the volumes folder joins one pool. People see one folder tree; files spread across the disks behind it. Add a disk any time; drain one to retire it.</p></div>
      <button class="btn" onclick={load}><Icon name="refresh" />Rescan mounts</button>
      {/if}
    {/if}
  </header>

  {#if !pool}<div class="card empty">Loading…</div>{:else}
  {#if session.protected_storage}
  <ProtectedStorage />
  {:else}
  <section class="card summary">
    <div class="row wrap" style="justify-content:space-between;gap:18px;align-items:flex-end">
      <div style="display:flex;flex-direction:column;gap:6px"><span class="muted" style="font-size:13px">{plain ? 'Server disk in use' : 'Pool'}</span><span class="big">{bytes(pool.used_bytes)} <span class="muted" style="font-size:18px;font-weight:500">of {bytes(pool.total_bytes, 0)}</span></span></div>
      <span class="tag {pool.mode === 'mergerfs' ? 'green' : ''}">{pool.mode === 'mergerfs' ? 'mergerfs pool' : 'Plain folders'}</span>
    </div>
    <div class="bigbar">
      {#if pool.mode === 'mergerfs'}
        {#each live as v, i}<div style="width:{pool.total_bytes ? (v.used_bytes / pool.total_bytes) * 100 : 0}%;background:{COLORS[i % 5]}"></div>{/each}
      {:else}<div style="width:{pool.total_bytes ? (pool.used_bytes / pool.total_bytes) * 100 : 0}%;background:var(--amber)"></div>{/if}
    </div>
  </section>

  {#if !plain}
  <section class="vols">
    {#each pool.volumes as v, i}
      {@const st = STATUS[v.status] || [v.status, '']}
      <article class="card vol" class:dashed={v.status === 'pending'}>
        <div class="row" style="justify-content:space-between;gap:10px">
          <div class="row" style="gap:10px"><span class="sw" style={['active', 'draining'].includes(v.status) ? `background:${COLORS[live.indexOf(v) % 5]}` : 'border:1.5px dashed #6B717A'}></span><span style="font-size:15px;font-weight:600">{v.name}</span></div>
          <span class="tag {st[1]}">{st[0]}</span>
        </div>
        <span class="mono note">{v.path}</span>
        <div style="display:flex;flex-direction:column;gap:8px">
          <div class="row" style="justify-content:space-between;font-size:13px"><span class="mono">{bytes(v.used_bytes)} used</span><span class="muted">{pool.mode === 'direct' ? 'shares the host disk' : bytes(v.total_bytes, 0)}</span></div>
          <div class="meter" style="height:8px;border-radius:4px"><div style="width:{v.total_bytes ? (v.used_bytes / v.total_bytes) * 100 : 0}%;background:{['active', 'draining'].includes(v.status) ? COLORS[live.indexOf(v) % 5] : '#4A4F57'}"></div></div>
        </div>
        {#if v.checks}
          <ul class="checks">
            {#each Object.entries(v.checks) as [k, ok]}<li><Icon name={ok ? 'check' : 'x'} size={14} style="color:{ok ? 'var(--green)' : 'var(--red)'}" />{CHECK[k] || k}</li>{/each}
          </ul>
        {/if}
        {#if v.drain}
          <div style="display:flex;flex-direction:column;gap:6px">
            <div class="row" style="justify-content:space-between;font-size:12px"><span class="muted">{v.status === 'drained' ? 'All files moved off' : 'Moving files'}</span><span class="mono">{v.status === 'drained' ? `${v.drain.moved} ${v.drain.moved === 1 ? 'file' : 'files'}` : `${v.drain.moved} / ${v.drain.total}`}</span></div>
            <div class="meter"><div style="width:{v.drain.total ? (v.drain.moved / v.drain.total) * 100 : 100}%;background:var(--amber)"></div></div>
          </div>
        {/if}
        {#if session.isAdmin}
          <div class="row" style="gap:8px;margin-top:auto">
            {#if v.status === 'pending'}<button class="btn pri grow" onclick={() => act(v, 'add')} disabled={v.checks && !Object.values(v.checks).every(Boolean)}><Icon name="plus" />Add to pool</button>
            {:else if v.status === 'active'}<button class="btn grow" onclick={() => (confirm = v)}>Drain</button>
            {:else if v.status === 'draining'}<button class="btn grow" onclick={() => act(v, 'undrain')}>Stop draining</button>{/if}
          </div>
        {/if}
        {#if v.added_at}<span class="note">In the pool since {ago(v.added_at)}</span>{/if}
      </article>
    {:else}
      <div class="card empty" style="grid-column:1/-1">No volumes found under the volumes folder.</div>
    {/each}
  </section>
  {/if}

  {/if}
  <section class="duo">
    {#if !session.protected_storage && !plain}
    <div class="card pad">
      <h2>Where new files go</h2>
      <div class="pols">
        {#each Object.entries(pool.policies) as [k, label]}
          <label class="pol" class:on={pool.policy === k}>
            <input type="radio" name="policy" value={k} checked={pool.policy === k} disabled={!session.isAdmin} onchange={() => policy(k)} />
            <span style="display:flex;flex-direction:column;gap:3px"><span style="font-weight:500;font-size:13px">{label}</span>
              <span class="note">{({ mfs: 'Each new file goes to the volume with the most free space. Best default.', ff: 'Fill the first volume, then the next. Keeps other disks idle.', epmfs: 'New files go where their folder already is, so a project stays on one disk.' })[k]}</span></span>
          </label>
        {/each}
      </div>
    </div>
    {/if}
    <form class="card pad" onsubmit={saveQuotas} style="display:flex;flex-direction:column;gap:14px">
      <h2>Default quotas</h2>
      <div class="row wrap" style="gap:12px">
        <label class="lbl grow">Each person (GB)<input class="field" type="number" min="0.1" step="0.1" bind:value={qf.user_gb} disabled={!session.isAdmin} /></label>
        <label class="lbl grow">Each team folder (GB)<input class="field" type="number" min="0.1" step="0.1" bind:value={qf.group_gb} disabled={!session.isAdmin} /></label>
      </div>
      <p class="note" style="margin:0">Change one person's quota on Users. Largest single file: {bytes(quotas?.max_file_bytes, 0)}. Older versions count too; when someone is near their limit, the oldest versions of the file they're saving make room first.</p>
      {#if session.isAdmin}<div><button class="btn pri">Save</button></div>{/if}
    </form>
  </section>

  {#if !session.protected_storage && !plain}
  <section class="card pad how">
    <h2>Adding a disk</h2>
    <ol>
      <li>Mount it on the host under <code>/srv/buckets/volumes/&lt;name&gt;</code> (ext4 or xfs; NFS works too).</li>
      <li>It appears here as <b>Waiting to join</b> after a rescan, with checks that it's writable.</li>
      <li>Press <b>Add to pool</b>. New files can land on it straight away; nothing restarts and nobody's paths change.</li>
      {#if pool.fstab}<li>Make it permanent: replace the pool line in <code>/etc/fstab</code> with the one below, so the pool comes back the same after a reboot.</li>{/if}
    </ol>
    {#if pool.fstab}<Copy text={pool.fstab} />{/if}
  </section>
  {/if}
  {/if}
</div>

{#if confirm}
  <Modal title="Drain {confirm.name}?" onclose={() => (confirm = null)}>
    <p style="margin:0;font-size:13px;line-height:1.6;color:var(--text-2)">New files stop landing on {confirm.name}, and its {bytes(confirm.used_bytes)} move to the other volumes in the background. Paths don't change and people keep working. When it shows <b>Drained</b>, update the pool line in <span class="mono">/etc/fstab</span> (shown on this page), then unmount it.</p>
    <div class="row" style="justify-content:flex-end"><button class="btn" onclick={() => (confirm = null)}>Cancel</button><button class="btn pri" onclick={() => act(confirm, 'drain')}>Start draining</button></div>
  </Modal>
{/if}

<style>
  .summary { padding: 22px 24px; display: flex; flex-direction: column; gap: 18px; }
  .big { font-size: 30px; font-weight: 600; letter-spacing: -0.03em; }
  .bigbar { height: 14px; border-radius: 7px; background: #1E2125; display: flex; gap: 2px; overflow: hidden; }
  .vols { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }
  .vol { padding: 20px; display: flex; flex-direction: column; gap: 14px; min-height: 220px; }
  .vol.dashed { border-style: dashed; border-color: #34383F; }
  .sw { width: 12px; height: 12px; border-radius: 4px; flex: none; }
  .checks { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: 1fr 1fr; gap: 6px; font-size: 12px; color: var(--text-2); }
  .checks li { display: flex; gap: 6px; align-items: center; }
  .duo { display: flex; flex-wrap: wrap; gap: 16px; }
  .duo > * { flex: 1 1 360px; }
  .pad { padding: 22px 24px; }
  .pols { display: flex; flex-direction: column; gap: 8px; margin-top: 14px; }
  .pol { display: flex; gap: 12px; align-items: flex-start; padding: 12px 14px; border-radius: 10px; border: 1px solid var(--line); cursor: pointer; }
  .pol.on { border-color: var(--amber-edge); background: #17130C; }
  .pol input { accent-color: var(--amber); margin-top: 2px; }
  .how ol { margin: 12px 0 0; padding-left: 20px; display: flex; flex-direction: column; gap: 8px; font-size: 13px; line-height: 1.55; color: var(--text-2); }
</style>
