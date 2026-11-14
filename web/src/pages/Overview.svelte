<script>
  import { api } from '../lib/api.js';
  import { session } from '../lib/session.svelte.js';
  import { fail } from '../lib/toast.svelte.js';
  import { bytes, ago } from '../lib/format.js';
  import { describe, VIA } from '../lib/activity.js';
  import Icon from '../lib/Icon.svelte';
  import Avatar from '../lib/Avatar.svelte';

  let days = $state(14);
  let d = $state(null);
  let sel = $state(null);
  $effect(() => { const n = days; sel = null; api.get('/api/overview', { days: n }).then((x) => (d = x)).catch(fail); });

  const series = $derived(d?.series || []);
  const activeDays = $derived(series.filter((s) => s.mcp + s.other > 0).length);
  const max = $derived(Math.max(1, ...series.map((s) => s.mcp + s.other)));
  const total = $derived(series.reduce((a, s) => a + s.mcp, 0));
  const pick = $derived(sel ?? series.length - 1);
  const label = (iso, long) => new Date(iso + 'T00:00:00').toLocaleDateString(undefined, long ? { weekday: 'short', day: 'numeric', month: 'short' } : { day: 'numeric', month: 'short' });
  let narrow = $state(typeof matchMedia !== 'undefined' && matchMedia('(max-width: 600px)').matches);
  $effect(() => {
    const mq = matchMedia('(max-width: 600px)');
    const on = () => (narrow = mq.matches);
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  });
  const every = $derived((days <= 7 ? 1 : days <= 14 ? 2 : 5) * (narrow ? 3 : 1));
  // Labels shown: every Nth day and the picked day, but never two side by side.
  const showLabel = (i) => i === pick || (i % every === 0 && Math.abs(i - pick) > (narrow ? 2 : 1));
  const COLORS = ['#F2A93B', '#8AA4FF', '#4CC38A', '#D59BE6', '#5CC8D0'];
  const today = new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' });
  const nvol = $derived(d ? d.volumes.filter((v) => v.status === 'active').length : 0);
</script>

<div class="page">
  <header class="page-head">
    <div style="display:flex;flex-direction:column;gap:6px"><span class="muted" style="font-size:13px">{today}</span><h1>Overview</h1></div>
    <div class="row wrap" style="gap:12px">
      <div class="segs" role="group" aria-label="Time range">
        {#each [[7, '7 days'], [14, '14 days'], [30, '30 days']] as [k, l]}
          <button class="seg" class:on={days === k} aria-pressed={days === k} onclick={() => (days = k)}>{l}</button>
        {/each}
      </div>
      {#if session.isAdmin}<a class="btn pri" href="/users?new=1"><Icon name="plus" />Add person</a>{/if}
    </div>
  </header>

  {#if !d}<div class="card empty">Loading…</div>{:else}
  <section class="kpis">
    <div class="card kpi"><span class="muted">People</span><span class="big">{d.people}</span><span class="note">active accounts</span></div>
    <div class="card kpi"><span class="muted">Files stored</span><span class="big">{d.files.toLocaleString()}</span><span class="note">{d.folders_with_grants} folders with their own sharing</span></div>
    <div class="card kpi"><span class="muted">Saved from Claude Code</span><span class="big" style="color:var(--amber)">{total.toLocaleString()}</span><span class="note">last {days} days</span></div>
    <div class="card kpi"><span class="muted">Pool capacity</span><span class="big">{d.pool.total_bytes ? Math.round((d.pool.used_bytes / d.pool.total_bytes) * 100) : 0}%</span><span class="note">{bytes(d.pool.used_bytes)} of {bytes(d.pool.total_bytes, 0)}{nvol ? ` on ${nvol} ${nvol === 1 ? 'disk' : 'disks'}` : ''}</span></div>
  </section>

  <section class="duo">
    <div class="card chart">
      <div class="row" style="justify-content:space-between;align-items:flex-start;gap:16px">
        <div style="display:flex;flex-direction:column;gap:4px"><h2>Files saved</h2><span class="note"><span class="dot" style="width:7px;height:7px;background:var(--amber)"></span> Claude Code &nbsp; <span class="dot" style="width:7px;height:7px;background:#3A3F48"></span> Browser and other</span></div>
        {#if series[pick]}
          <div style="text-align:right;display:flex;flex-direction:column;gap:4px">
            <span class="mono" style="font-size:22px;font-weight:500">{series[pick].mcp + series[pick].other}</span>
            <span class="note">{label(series[pick].date, true)} · {series[pick].mcp} from Claude Code</span>
          </div>
        {/if}
      </div>
      {#if activeDays < 2}
        <div class="plot-empty">
          <span class="mono big">{series.reduce((a, s) => a + s.mcp + s.other, 0)}</span>
          <span class="note">{activeDays === 0 ? 'No files saved in this period yet.' : `files saved so far, all on ${label(series.find((s) => s.mcp + s.other > 0).date, true)}.`}<br />The daily chart fills in once files arrive on more than one day.</span>
        </div>
      {:else}
      <div class="plot" style="gap:{days > 14 ? 4 : 10}px">
        <div class="gl" style="top:0"></div><div class="gl" style="top:50%"></div>
        {#each series as s, i}
          <button class="col" onclick={() => (sel = i)} aria-label="{label(s.date, true)}: {s.mcp} from Claude Code, {s.other} other">
            <span class="stack" style="height:{Math.max(3, ((s.mcp + s.other) / max) * 100)}%">
              <span style="flex:{s.other};background:{i === pick ? '#5A5F68' : '#30343B'}"></span>
              <span style="flex:{s.mcp || (s.other ? 0 : 1)};background:{i === pick ? 'var(--amber)' : s.mcp ? '#9C6E2A' : '#24272C'}"></span>
            </span>
          </button>
        {/each}
      </div>
      <div class="xl" style="gap:{days > 14 ? 4 : 10}px">
        {#each series as s, i}<span class="mono" style:color={i === pick ? 'var(--text)' : ''}>{showLabel(i) ? label(s.date) : ''}</span>{/each}
      </div>
      {/if}
    </div>

    <div class="card side">
      <div class="row" style="justify-content:space-between"><h2>Storage pool</h2><a href="/storage" style="font-size:13px">Manage</a></div>
      <div class="row" style="align-items:baseline;gap:8px"><span style="font-size:28px;font-weight:600;letter-spacing:-0.03em">{bytes(d.pool.total_bytes - d.pool.used_bytes)}</span><span class="note">free of {bytes(d.pool.total_bytes, 0)}</span></div>
      <div class="bar">
        {#each d.volumes.filter((v) => v.status === 'active' || v.status === 'draining') as v, i}
          <div style="width:{d.pool.total_bytes ? (v.used_bytes / d.pool.total_bytes) * 100 : 0}%;background:{COLORS[i % 5]}"></div>
        {/each}
        {#if d.pool.mode !== 'mergerfs'}<div style="width:{d.pool.total_bytes ? (d.pool.used_bytes / d.pool.total_bytes) * 100 : 0}%;background:var(--amber)"></div>{/if}
      </div>
      <div>
        {#each d.volumes as v, i}
          <div class="vol">
            <span class="sw" style={v.status === 'active' || v.status === 'draining' ? `background:${COLORS[i % 5]}` : 'border:1.5px dashed #6B717A'}></span>
            <span class="grow" style="display:flex;flex-direction:column;gap:2px"><span style="font-size:13px;font-weight:500">{v.name}</span><span class="note">{v.label || ({ pending: 'Detected, not in pool', draining: 'Draining', drained: 'Drained, safe to remove', missing: 'Not mounted' })[v.status] || 'In pool'}</span></span>
            <span class="mono note" style="color:var(--text-2)">{d.pool.mode === 'direct' ? bytes(v.used_bytes) : `${bytes(v.used_bytes)} / ${bytes(v.total_bytes, 0)}`}</span>
          </div>
        {:else}
          <div class="note" style="padding:12px 0">{d.pool.mode === 'mergerfs' ? 'No volumes mounted yet.' : 'Files are kept in plain folders on this server.'}</div>
        {/each}
      </div>
    </div>
  </section>

  <section class="duo">
    <div class="card act">
      <div class="row" style="justify-content:space-between;padding-bottom:6px"><h2>Recent activity</h2><a href="/audit" style="font-size:13px">Audit log</a></div>
      {#each d.activity as e}
        {@const [verb, obj, rest] = describe(e)}
        <div class="ev">
          <Avatar name={e.actor_name || e.actor} />
          <div class="grow" style="display:flex;flex-direction:column;gap:4px">
            <span style="font-size:13px;line-height:1.5"><b>{e.actor_name || e.actor}</b> {verb} {#if obj}<span class="mono" style="color:var(--amber)">{obj}</span>{/if} {rest}</span>
            <span class="note">{e.detail?.token ? e.detail.token.replace(/ \(…\w+\)$/, '') : VIA[e.via] || e.via}{e.detail?.project ? ` · project ${e.detail.project}` : ''}</span>
          </div>
          <span class="note" style="white-space:nowrap">{ago(e.ts)}</span>
        </div>
      {:else}<div class="empty">No activity yet.</div>{/each}
    </div>
    <div class="card side">
      <h2>Needs attention</h2>
      {#each d.attention as a}
        <a class="att" href={a.link}>
          <span class="dot" style="background:{({ volume: 'var(--blue)', tokens: 'var(--amber)', smb: 'var(--red)', directory: 'var(--violet)' })[a.kind]};margin-top:6px"></span>
          <span style="font-size:13px;font-weight:500;line-height:1.5">{a.text}</span>
        </a>
      {:else}
        <div class="calm"><Icon name="check" style="color:var(--green)" /><span>All clear. Storage, tokens{session.smb ? ', share permissions' : ''} and the directory look healthy.</span></div>
      {/each}
    </div>
  </section>
  {/if}
</div>

<style>
  .plot-empty { min-height: 200px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; text-align: center; border: 1px dashed var(--line, #2a2d33); border-radius: 10px; margin-top: 14px; }
  .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; }
  .kpi { padding: 20px 22px; display: flex; flex-direction: column; gap: 14px; font-size: 13px; }
  .big { font-size: 34px; font-weight: 600; letter-spacing: -0.03em; line-height: 1; }
  .duo { display: flex; flex-wrap: wrap; gap: 16px; }
  .chart { flex: 2 1 560px; min-width: 0; padding: 22px 24px 20px; display: flex; flex-direction: column; gap: 22px; }
  .plot { position: relative; height: 200px; display: flex; align-items: flex-end; border-bottom: 1px solid #25282D; }
  .gl { position: absolute; left: 0; right: 0; border-top: 1px dashed #1F2226; }
  .col { all: unset; cursor: pointer; flex: 1; height: 100%; display: flex; align-items: flex-end; position: relative; }
  .stack { width: 100%; display: flex; flex-direction: column; border-radius: 5px 5px 2px 2px; overflow: hidden; transition: filter .15s; }
  .col:hover .stack { filter: brightness(1.25); }
  .col:focus-visible { outline: 2px solid var(--amber); outline-offset: 2px; }
  .xl { display: flex; margin-top: -12px; overflow: hidden; }
  .xl span:first-child { text-align: left; }
  .xl span:last-child { text-align: right; }
  .xl span { flex: 1; text-align: center; font-size: 11px; color: #8A919A; white-space: nowrap; overflow: visible; }
  .side { flex: 1 1 300px; min-width: 0; padding: 22px 24px; display: flex; flex-direction: column; gap: 16px; }
  .bar { height: 12px; border-radius: 6px; background: #1E2125; display: flex; gap: 2px; overflow: hidden; }
  .vol { display: flex; align-items: center; gap: 10px; padding: 12px 0; border-bottom: 1px solid var(--line-soft); }
  .vol:last-child { border-bottom: 0; }
  .sw { width: 10px; height: 10px; border-radius: 3px; flex: none; }
  .act { flex: 2 1 560px; min-width: 0; padding: 22px 24px 8px; }
  .ev { display: flex; gap: 14px; align-items: flex-start; padding: 14px 0; border-bottom: 1px solid var(--line-soft); }
  .ev:last-child { border-bottom: 0; }
  .ev b { font-weight: 600; }
  .att { display: flex; gap: 12px; align-items: flex-start; padding: 14px 16px; border-radius: 10px; background: var(--card-2); border: 1px solid var(--line); color: var(--text); }
  .att:hover { border-color: var(--line-hover); color: var(--text); }
  .calm { display: flex; gap: 10px; align-items: flex-start; font-size: 13px; color: var(--text-2); line-height: 1.55; padding: 14px 16px; border-radius: 10px; background: #121A16; border: 1px solid #1F2E26; }
</style>
