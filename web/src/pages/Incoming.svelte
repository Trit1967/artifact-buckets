<script>
  import { api } from '../lib/api.js';
  import { session } from '../lib/session.svelte.js';
  import { toast, fail } from '../lib/toast.svelte.js';
  import { ago, bytes } from '../lib/format.js';
  import Icon from '../lib/Icon.svelte';
  import Modal from '../lib/Modal.svelte';

  let data = $state(null);
  let rules = $state([]);
  let canAdd = $state(false);
  let filter = $state('all');
  let dialog = $state(null);
  let rule = $state({ match: 'git_remote', pattern: '', target: '', priority: 100 });
  let test = $state({ git_remote: '', project: '' });
  let testOut = $state(null);

  async function load() {
    try {
      data = await api.get('/api/ingest', { status: filter, limit: 100 });
      const r = await api.get('/api/routing');
      rules = r.rules; canAdd = r.can_add;
    } catch (e) { fail(e); }
  }
  $effect(() => { filter; load(); });
  const t = setInterval(load, 15000);
  $effect(() => () => clearInterval(t));

  async function togglePause() {
    try { await api.put('/api/ingest/settings', { paused: !data.paused }); toast(data.paused ? 'Claude Code saves resumed' : 'Claude Code saves paused'); load(); } catch (e) { fail(e); }
  }
  async function addRule(e) {
    e.preventDefault();
    try { await api.post('/api/routing', { ...rule, priority: Number(rule.priority) }); dialog = null; toast('Rule added'); load(); } catch (e) { fail(e); }
  }
  async function delRule(r) { try { await api.del(`/api/routing/${r.id}`); toast('Rule removed'); load(); } catch (e) { fail(e); } }
  async function runTest(e) {
    e.preventDefault();
    try { testOut = await api.post('/api/routing/preview', test); } catch (e) { fail(e); }
  }
  const reason = (r) => r === 'project' ? 'your projects folder' : r === 'inbox' ? 'your inbox' : r === 'path' || r === 'folder' ? 'the folder asked for' : r?.startsWith('rule') ? `routing rule ${r.slice(5)}` : r;
</script>

<div class="page">
  <header class="page-head">
    <div><h1>Claude Code saves</h1><p class="sub">Everything Claude Code saved{session.isAuditor ? ' across the team' : ' for you'}, where it went and why. Updates every 15 seconds.</p></div>
    {#if session.isAdmin && data}
      <button class="btn" class:pri={data.paused} onclick={togglePause}><Icon name={data.paused ? 'play' : 'pause'} />{data.paused ? 'Resume saves' : 'Pause saves'}</button>
    {/if}
  </header>

  {#if data?.paused}
    <div class="banner"><Icon name="pause" /><span><b>Saves from Claude Code are paused.</b> Claude gets a clear message and keeps the file locally. Uploads in the browser still work.</span></div>
  {/if}

  {#if data}
    <section class="stats">
      <div class="card stat"><span class="muted">Saved, last 24 hours</span><span class="big" style="color:var(--amber)">{data.today.saved}</span><span class="note">{session.isAuditor ? 'by everyone' : 'by you'}</span></div>
      <div class="card stat"><span class="muted">Refused, last 24 hours</span><span class="big" style:color={data.today.rejected ? 'var(--red)' : ''}>{data.today.rejected}</span><span class="note">No access, over quota, too large or paused</span></div>
      <div class="card stat"><span class="muted">Largest file accepted</span><span class="big">{bytes(data.max_file_bytes, 0)}</span><span class="note">Per file. Larger ones are refused with a reason.</span></div>
    </section>
  {/if}

  <section class="card">
    <div class="hd">
      <div style="display:flex;flex-direction:column;gap:4px"><h2>Where saves go</h2><span class="note">Checked in order. The first rule that matches, and that the person can write to, wins.</span></div>
      {#if canAdd}<button class="btn" onclick={() => { rule = { match: 'git_remote', pattern: '', target: '', priority: 100 }; dialog = 'rule'; }}><Icon name="plus" />Add rule</button>{/if}
    </div>
    <ol class="flow">
      <li><span class="n mono">1</span><span><b>A folder named in the request.</b> “Save this to /shared/design/specs”.</span></li>
      {#each rules as r}
        <li>
          <span class="n mono"><Icon name="route" size={13} /></span>
          <span class="grow">When the {r.match === 'git_remote' ? 'repository' : 'project'} matches <code>{r.pattern}</code>, save to <code>{r.target}</code></span>
          <span class="note mono">#{r.id} · priority {r.priority}</span>
          {#if r.can_edit}<button class="btn icon sm" aria-label="Remove rule {r.id}" onclick={() => delRule(r)}><Icon name="x" size={14} /></button>{/if}
        </li>
      {/each}
      <li><span class="n mono">{rules.length ? 3 : 2}</span><span>Otherwise <code>/users/&lt;you&gt;/projects/&lt;project&gt;</code>, named after the repository folder.</span></li>
      <li><span class="n mono">{rules.length ? 4 : 3}</span><span>No project at all: <code>/users/&lt;you&gt;/inbox</code>.</span></li>
    </ol>
    <form class="tester" onsubmit={runTest}>
      <span class="sec">Try it</span>
      <div class="row wrap" style="gap:8px">
        <input class="field mono grow" style="min-width:220px" placeholder="git@git.lab.local:platform/latency-svc.git" bind:value={test.git_remote} aria-label="Git remote" />
        <input class="field mono" style="width:180px" placeholder="project" bind:value={test.project} aria-label="Project" />
        <button class="btn">Where would it go?</button>
      </div>
      {#if testOut}<div class="row" style="font-size:13px"><Icon name="move" style="color:var(--amber)" /><span class="mono">{testOut.folder}</span><span class="note">via {reason(testOut.reason)} (for you)</span></div>{/if}
    </form>
  </section>

  <section class="card">
    <div class="hd">
      <h2>Recent saves</h2>
      <div class="segs" role="group" aria-label="Filter">
        {#each [['all', 'All'], ['saved', 'Saved'], ['rejected', 'Refused']] as [k, l]}
          <button class="seg" class:on={filter === k} aria-pressed={filter === k} onclick={() => (filter = k)}>{l}</button>
        {/each}
      </div>
    </div>
    {#if !data}<div class="empty">Loading…</div>
    {:else if !data.feed.length}<div class="empty">Nothing yet. Once someone connects Claude Code and saves, it shows up here.</div>
    {:else}
      <div class="scroll-x"><table class="table">
        <thead><tr><th>When</th><th>File</th><th>Folder</th>{#if session.isAuditor}<th>Person</th>{/if}<th>Project</th><th>Result</th></tr></thead>
        <tbody>
          {#each data.feed as f}
            <tr>
              <td class="muted" style="white-space:nowrap">{ago(f.ts)}</td>
              <td style="font-weight:500">{f.path.split('/').pop()}</td>
              <td class="mono note">{#if f.ok}<a href={`/files?path=${encodeURIComponent(f.path.split('/').slice(0, -1).join('/'))}&file=${encodeURIComponent(f.path)}`} style="color:var(--text-2)">{f.path.split('/').slice(0, -1).join('/')}</a>{:else}{f.path.split('/').slice(0, -1).join('/')}{/if}</td>
              {#if session.isAuditor}<td>{f.user}</td>{/if}
              <td class="mono note">{f.detail?.project || '—'}</td>
              <td>{#if f.ok}<span class="tag green">{f.detail?.unchanged ? 'Unchanged' : `Saved v${f.detail?.version ?? 1}`}</span>{:else}<span class="tag red">{f.detail?.reason || 'Refused'}</span>{/if}</td>
            </tr>
          {/each}
        </tbody>
      </table></div>
    {/if}
  </section>
</div>

{#if dialog === 'rule'}
  <Modal title="Add a routing rule" onclose={() => (dialog = null)}>
    <form onsubmit={addRule} style="display:flex;flex-direction:column;gap:14px">
      <label class="lbl">Match on<select class="field" bind:value={rule.match}><option value="git_remote">Git repository</option><option value="project">Project name</option></select></label>
      <label class="lbl">Pattern<input class="field mono" bind:value={rule.pattern} required placeholder={rule.match === 'git_remote' ? 'git.lab.local/platform/*' : 'billing-*'} /></label>
      <label class="lbl">Save to<input class="field mono" bind:value={rule.target} required placeholder="/shared/platform/projects/{'{project}'}" /></label>
      <label class="lbl" style="width:140px">Priority<input class="field" type="number" bind:value={rule.priority} /></label>
      <p class="note" style="margin:0">Use <code>*</code> as a wildcard. The target may use <code>{'{project}'}</code> and <code>{'{user}'}</code>. Repository addresses are compared without the protocol and <code>.git</code>, so SSH and HTTPS remotes match the same rule.</p>
      <div class="row" style="justify-content:flex-end"><button type="button" class="btn" onclick={() => (dialog = null)}>Cancel</button><button class="btn pri">Add rule</button></div>
    </form>
  </Modal>
{/if}

<style>
  .banner { display: flex; gap: 12px; align-items: flex-start; padding: 14px 18px; border-radius: 12px; background: #1D170D; border: 1px solid var(--amber-edge); font-size: 13px; line-height: 1.5; }
  .banner :global(.ic) { color: var(--amber); margin-top: 2px; }
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; }
  .stat { padding: 20px 22px; display: flex; flex-direction: column; gap: 12px; font-size: 13px; }
  .big { font-size: 32px; font-weight: 600; letter-spacing: -0.03em; line-height: 1; }
  .hd { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 18px 22px; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
  .flow { list-style: none; margin: 0; padding: 8px 22px; }
  .flow li { display: flex; gap: 14px; align-items: center; padding: 12px 0; border-bottom: 1px solid var(--line-soft); font-size: 13px; line-height: 1.5; }
  .flow li:last-child { border-bottom: 0; }
  .n { flex: none; width: 24px; height: 24px; border-radius: 7px; background: var(--raise); color: var(--text-2); display: grid; place-items: center; font-size: 11px; }
  .tester { margin: 0 22px 20px; padding: 14px; border-radius: 10px; background: var(--card-2); border: 1px solid var(--line); display: flex; flex-direction: column; gap: 10px; }
  .segs { display: flex; gap: 2px; padding: 3px; border-radius: 10px; background: var(--card); border: 1px solid var(--line); }
</style>
