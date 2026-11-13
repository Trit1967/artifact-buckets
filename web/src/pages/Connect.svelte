<script>
  import { api } from '../lib/api.js';
  import { session } from '../lib/session.svelte.js';
  import { toast, fail } from '../lib/toast.svelte.js';
  import { ago, when } from '../lib/format.js';
  import Icon from '../lib/Icon.svelte';
  import Copy from '../lib/Copy.svelte';
  import Modal from '../lib/Modal.svelte';

  let tokens = $state(null);
  let dialog = $state(null);
  let created = $state(null);
  let form = $state({ name: '', scope: 'write', folders: '', days: 30 });

  let everyone = $state(false);
  async function load() { try { tokens = (await api.get('/api/tokens', everyone ? { all: true } : {})).tokens; } catch (e) { fail(e); } }
  load();
  const devices = $derived((tokens || []).filter((t) => t.kind === 'device'));
  const personal = $derived((tokens || []).filter((t) => t.kind === 'personal'));

  async function revoke(t) {
    try { await api.del(`/api/tokens/${t.id}`); toast(t.kind === 'device' ? `Disconnected ${t.name}` : `Revoked ${t.name}`); load(); } catch (e) { fail(e); }
  }
  async function create(e) {
    e.preventDefault();
    try {
      const folders = form.folders.split(/[\n,]/).map((s) => s.trim()).filter(Boolean);
      created = await api.post('/api/tokens', { name: form.name, scope: form.scope, folders: folders.length ? folders : null, days: Number(form.days) });
      dialog = null; load();
    } catch (e) { fail(e); }
  }
  const mcpUrl = `${session.base_url}/mcp`;
</script>

<div class="page">
  <header class="page-head">
    <div><h1>Connect Claude Code</h1><p class="sub">Once connected, Claude Code saves what it makes straight into your folders, and can find, organize and share them for you.</p></div>
  </header>

  <section class="steps">
    <div class="card step">
      <span class="num mono">1</span>
      <h2>Install the plugin</h2>
      <p class="note">In Claude Code, run these two commands. The plugin brings the connection and the skills that teach Claude how to use your folders.</p>
      <Copy text={`/plugin marketplace add ${session.marketplace}`} />
      <Copy text="/plugin install artifact-buckets@lab-tools" />
    </div>
    <div class="card step">
      <span class="num mono">2</span>
      <h2>Sign in once</h2>
      <p class="note">In Claude Code run <code>/mcp</code>, choose <b>artifact-buckets</b>, then <b>Authenticate</b>. Your browser opens this site; sign in and press <b>Connect</b>. No keys to copy. <code>/buckets-setup</code> checks it worked.</p>
      <div class="mock">
        <div class="row" style="gap:8px"><span class="dot" style="background:var(--amber)"></span><span class="mono" style="font-size:12px">/mcp → artifact-buckets → Authenticate</span></div>
        <div class="mono note" style="padding-left:16px">Browser: Connect Claude Code to Artifact Buckets? → Connect</div>
        <div class="mono" style="padding-left:16px;font-size:12px;color:var(--green)">Authentication successful. Connected to artifact-buckets.</div>
      </div>
    </div>
    <div class="card step">
      <span class="num mono">3</span>
      <h2>Work as usual</h2>
      <p class="note">Files land in <span class="mono">projects/&lt;repo&gt;</span> under your home folder, or in a team folder when your team has a rule for that repository. Ask for anything:</p>
      <ul class="eg">
        <li>“Save this report to the platform team folder”</li>
        <li>“Find the diagrams I made last week”</li>
        <li>“Share the q3 folder with finance as viewers”</li>
      </ul>
    </div>
  </section>

  <details class="card alt">
    <summary>Without the plugin</summary>
    <div style="display:flex;flex-direction:column;gap:10px;padding-top:12px">
      <p class="note" style="margin:0">Add the server by hand, then sign in the same way: <code>/mcp</code>, <b>artifact-buckets</b>, <b>Authenticate</b>.</p>
      <Copy text={`claude mcp add --transport http artifact-buckets ${mcpUrl}`} />
    </div>
  </details>

  <section class="card">
    <div class="hd">
      <h2>{everyone ? 'Everyone’s connected computers' : 'Your connected computers'}</h2>
      <span class="row" style="gap:12px"><span class="note">{devices.length} connected</span>
        {#if session.isAdmin}<div class="segs" role="group" aria-label="Whose"><button class="seg" class:on={!everyone} onclick={() => { everyone = false; load(); }}>Mine</button><button class="seg" class:on={everyone} onclick={() => { everyone = true; load(); }}>Everyone</button></div>{/if}
      </span>
    </div>
    {#if !tokens}<div class="empty">Loading…</div>
    {:else if !devices.length}<div class="empty">No computers yet. Follow the steps above and this list fills in.</div>
    {:else}
      {#each devices as d}
        <div class="dev">
          <span class="dvi"><Icon name="laptop" /></span>
          <span class="grow" style="display:flex;flex-direction:column;gap:3px">
            <span style="font-weight:500">{d.name}{#if everyone} <span class="note">· {d.user}</span>{/if}</span>
            <span class="note">Connected {ago(d.created_at)} · last used {d.last_used ? ago(d.last_used) : 'not yet'} · stays signed in until {when(d.expires_at)} unless unused</span>
          </span>
          <button class="btn sm danger" onclick={() => revoke(d)}>Disconnect</button>
        </div>
      {/each}
    {/if}
  </section>

  <section class="card">
    <div class="hd">
      <div style="display:flex;flex-direction:column;gap:4px"><h2>Personal tokens</h2><span class="note">For build servers and scripts that can't open a browser. Admin tools never work with these.</span></div>
      <button class="btn" onclick={() => { form = { name: '', scope: 'write', folders: '', days: 30 }; dialog = 'new'; }}><Icon name="key" />New token</button>
    </div>
    {#if personal.length}
      <div class="scroll-x"><table class="table">
        <thead><tr><th>Name</th><th>Can</th><th>Limited to</th><th>Last used</th><th>Expires</th><th></th></tr></thead>
        <tbody>
          {#each personal as t}
            <tr>
              <td><span style="font-weight:500">{t.name}</span> <span class="mono note">…{t.hint}</span>{#if everyone}<div class="note">{t.user}</div>{/if}</td>
              <td><span class="tag {t.scope === 'read' ? 'blue' : 'green'}">{t.scope === 'read' ? 'Read' : 'Read and write'}</span></td>
              <td class="mono note">{t.folders?.join(', ') || 'Everything you can reach'}</td>
              <td class="muted">{t.last_used ? ago(t.last_used) : 'Never'}</td>
              <td class:warn={t.expires_in_days <= 7} title={when(t.expires_at)}>{t.expires_in_days <= 0 ? 'Today' : t.expires_in_days === 1 ? 'Tomorrow' : `${t.expires_in_days} days`}</td>
              <td style="text-align:right"><button class="btn sm danger" onclick={() => revoke(t)}>Revoke</button></td>
            </tr>
          {/each}
        </tbody>
      </table></div>
    {:else if tokens}<div class="empty">No personal tokens.</div>{/if}
  </section>
</div>

{#if dialog === 'new'}
  <Modal title="New personal token" onclose={() => (dialog = null)}>
    <form onsubmit={create} style="display:flex;flex-direction:column;gap:14px">
      <label class="lbl">Name<input class="field" bind:value={form.name} placeholder="e.g. nightly report job" required maxlength="128" /></label>
      <div class="row" style="gap:12px">
        <label class="lbl grow">Can<select class="field" bind:value={form.scope}><option value="write">Read and write</option><option value="read">Read only</option></select></label>
        <label class="lbl" style="width:140px">Days<input class="field" type="number" min="1" max="365" bind:value={form.days} /></label>
      </div>
      <label class="lbl">Only these folders (optional, one per line)<textarea class="field" rows="3" style="height:auto;padding:10px 12px;font-family:var(--mono)" bind:value={form.folders} placeholder={`/users/${session.user.username}/projects/reports`}></textarea></label>
      <div class="row" style="justify-content:flex-end"><button type="button" class="btn" onclick={() => (dialog = null)}>Cancel</button><button class="btn pri">Create token</button></div>
    </form>
  </Modal>
{/if}
{#if created}
  <Modal title="Copy your token now" onclose={() => (created = null)}>
    <p style="margin:0;font-size:13px;line-height:1.55;color:var(--text-2)">This is the only time it's shown. Store it in your build server's secret store.</p>
    <Copy text={created.token} label="token" />
    <p class="note" style="margin:0">Use it as <span class="mono">Authorization: Bearer …</span> against <span class="mono">{mcpUrl}</span> or the REST API.</p>
    <div class="row" style="justify-content:flex-end"><button class="btn pri" onclick={() => (created = null)}>Done</button></div>
  </Modal>
{/if}

<style>
  .steps { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }
  .step { padding: 22px; display: flex; flex-direction: column; gap: 12px; position: relative; }
  .step p { margin: 0; }
  .num { width: 26px; height: 26px; border-radius: 8px; background: var(--amber-bg); color: var(--amber); display: grid; place-items: center; font-size: 12px; font-weight: 500; }
  .mock { padding: 12px 14px; border-radius: 10px; background: #0F1012; border: 1px solid var(--line); display: flex; flex-direction: column; gap: 6px; }
  .eg { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--text-2); }
  .alt { padding: 16px 22px; }
  .alt summary { cursor: pointer; font-size: 13px; font-weight: 500; color: var(--text-2); }
  .hd { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 18px 22px; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
  .dev { display: flex; align-items: center; gap: 14px; padding: 14px 22px; border-bottom: 1px solid var(--line-soft); font-size: 13px; }
  .dev:last-child { border-bottom: 0; }
  .dvi { width: 34px; height: 34px; border-radius: 9px; background: var(--raise); color: var(--text-2); display: grid; place-items: center; }
  .warn { color: var(--amber); }
</style>
