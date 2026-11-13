<script>
  import { api } from '../lib/api.js';
  import { session } from '../lib/session.svelte.js';
  import { toast, fail } from '../lib/toast.svelte.js';
  import { ago, agoPhrase, when } from '../lib/format.js';
  import Icon from '../lib/Icon.svelte';
  import Copy from '../lib/Copy.svelte';
  import Modal from '../lib/Modal.svelte';

  let tokens = $state(null);
  let dialog = $state(null);
  let created = $state(null);
  let stopping = $state(null);
  let form = $state({ name: '', scope: 'write', folders: '', days: 30 });

  let everyone = $state(false);
  async function load() { try { tokens = (await api.get('/api/tokens', everyone ? { all: true } : {})).tokens; } catch (e) { fail(e); } }
  load();
  const devices = $derived((tokens || []).filter((t) => t.kind === 'device'));
  const personal = $derived((tokens || []).filter((t) => t.kind === 'personal'));
  const syncers = $derived((tokens || []).filter((t) => t.kind === 'sync'));
  let sync = $state(null);
  api.get('/api/sync/status', null, { quiet: true }).then((d) => (sync = d)).catch(() => {});
  const home = `/users/${session.user.username}`;

  async function revoke(t) {
    try {
      await api.del(`/api/tokens/${t.id}`);
      toast(t.kind === 'device' ? `Disconnected ${t.name}` : t.kind === 'sync' ? `${t.name.replace(/^Sync on /, '')} no longer syncs` : `Revoked ${t.name}`);
      load();
    } catch (e) { fail(e); }
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
  const setup = session.claude || {};
  let os = $state(/Windows/i.test(navigator.userAgent) ? 'windows' : 'unix');
</script>

<div class="page">
  <header class="page-head">
    <div><h1>Connect Claude Code</h1><p class="sub">Once connected, Claude Code saves what it makes straight into your folders, and can find, organize and share them for you.</p></div>
  </header>

  <section class="steps">
    <div class="card step">
      <span class="num mono">1</span>
      <h2>Run one line</h2>
      <div class="segs os" role="group" aria-label="Your computer">
        <button class="seg" class:on={os === 'windows'} onclick={() => (os = 'windows')}>Windows</button>
        <button class="seg" class:on={os === 'unix'} onclick={() => (os = 'unix')}>macOS and Linux</button>
      </div>
      <Copy text={os === 'windows' ? setup.windows : setup.unix} label="the setup command" />
      <p class="note">Paste it into {os === 'windows' ? 'PowerShell' : 'a terminal'}. It adds the Artifact Buckets plugin to Claude Code, already pointed at this server. Running it again is safe.{#if setup.private_ca}{' '}This lab server uses its own certificate, so the first time, {os === 'windows' ? 'Windows' : 'your computer'} asks you to trust it once.{/if}</p>
      <div class="or"><span>or ask Claude Code</span></div>
      <Copy text={setup.prompt} label="the prompt" />
      <p class="note">Paste it into Claude Code. Claude reads the steps, checks them, explains each one and does them for you.</p>
    </div>
    <div class="card step">
      <span class="num mono">2</span>
      <h2>Sign in once</h2>
      <p class="note">Your browser opens this site. Sign in and press <b>Connect</b>. There are no keys to copy, and nothing to type into Claude Code.</p>
      <div class="mock">
        <div class="mono" style="font-size:12px">Plugin installed</div>
        <div class="mono note">Opening your browser to sign in…</div>
        <div class="mono" style="font-size:12px;color:var(--green)">Done. Claude Code can now save to Artifact Buckets.</div>
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

  {#if session.isAdmin}
    <section class="card rollout">
      <div class="hd">
        <div style="display:flex;flex-direction:column;gap:4px">
          <h2>Set up every computer at once</h2>
          <span class="note">Push this policy with Intune, Group Policy or Jamf. Claude Code installs the plugin by itself at its next start, and people only sign in once when it asks.</span>
        </div>
        <a class="btn" href="/claude/managed-settings.json" download="managed-settings.json"><Icon name="download" />Download</a>
      </div>
      <div class="ro-body">
        <pre class="mono policy">{JSON.stringify(setup.managed_settings, null, 2)}</pre>
        <dl class="where">
          <dt>Windows</dt><dd class="mono">C:\Program Files\ClaudeCode\managed-settings.json</dd>
          <dt>Group Policy or Intune</dt><dd>The same JSON as the <span class="mono">Settings</span> value under <span class="mono">HKLM\SOFTWARE\Policies\ClaudeCode</span></dd>
          <dt>macOS</dt><dd class="mono">/Library/Application Support/ClaudeCode/managed-settings.json</dd>
          <dt>Linux</dt><dd class="mono">/etc/claude-code/managed-settings.json</dd>
        </dl>
        {#if setup.private_ca}<p class="note" style="margin:0">Computers need to trust this server’s certificate first. Domain-joined PCs already trust a certificate from your own certificate authority.</p>{/if}
      </div>
    </section>
  {/if}

  <details class="card alt">
    <summary>Set it up by hand</summary>
    <div style="display:flex;flex-direction:column;gap:10px;padding-top:12px">
      <p class="note" style="margin:0">With the plugin (the connection, plus skills that teach Claude your folders):</p>
      <Copy text={`claude plugin marketplace add ${setup.marketplace}`} />
      <Copy text={`claude plugin install ${setup.plugin}`} />
      <p class="note" style="margin:6px 0 0">Or only the connection:</p>
      <Copy text={`claude mcp add --transport http artifact-buckets ${mcpUrl}`} />
      <p class="note" style="margin:0">Then sign in with <code>/mcp</code> in Claude Code, choose <b>artifact-buckets</b> and <b>Authenticate</b>.</p>
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

  <section class="card" id="sync">
    <div class="hd">
      <div style="display:flex;flex-direction:column;gap:4px">
        <h2>{everyone ? 'Everyone’s computers that sync' : 'Computers that sync'}</h2>
        <span class="note">They copy the files Claude Code makes into {everyone ? 'each person’s' : 'your'} <span class="mono">projects</span> and <span class="mono">inbox</span>, after each reply. They can only add files.</span>
      </div>
      {#if !everyone && sync?.files}<a class="btn" href={`/files?path=${encodeURIComponent(home + '/projects')}`}><Icon name="folder" />{sync.files} synced {sync.files === 1 ? 'file' : 'files'}</a>{/if}
    </div>
    {#if !tokens}<div class="empty">Loading…</div>
    {:else if !syncers.length}<div class="empty">None yet. The setup line above offers to bring in what Claude has already made on a computer, and keeps it in sync.</div>
    {:else}
      {#each syncers as d}
        <div class="dev">
          <span class="dvi"><Icon name="refresh" /></span>
          <span class="grow" style="display:flex;flex-direction:column;gap:3px">
            <span style="font-weight:500">{d.name.replace(/^Sync on /, '')}{#if everyone} <span class="note">· {d.user}</span>{/if}</span>
            <span class="note">Linked {agoPhrase(d.created_at)} · last copied {d.last_used ? agoPhrase(d.last_used) : 'nothing yet'}</span>
          </span>
          <button class="btn sm danger" onclick={() => (stopping = d)}>Turn off</button>
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
{#if stopping}
  <Modal title={`Stop ${stopping.name.replace(/^Sync on /, '')} from syncing?`} onclose={() => (stopping = null)}>
    <p style="margin:0;font-size:13px;line-height:1.55;color:var(--text-2);overflow-wrap:anywhere">It stops copying new files at once. Files already copied stay where they are. To sync it again later, link it again from the computer.</p>
    <div class="row" style="justify-content:flex-end"><button class="btn" onclick={() => (stopping = null)}>Keep syncing</button><button class="btn danger" onclick={() => { const d = stopping; stopping = null; revoke(d); }}>Turn off</button></div>
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
  .os { align-self: flex-start; }
  .or { display: flex; align-items: center; gap: 10px; color: var(--faint); font-size: 12px; margin-top: 4px; }
  .or::before, .or::after { content: ''; flex: 1; height: 1px; background: var(--line); }
  .rollout .ro-body { padding: 18px 22px 20px; display: flex; flex-direction: column; gap: 16px; }
  .policy { margin: 0; padding: 14px 16px; border-radius: 10px; background: #0F1012; border: 1px solid var(--line); font-size: 12.5px; line-height: 1.55; color: var(--text); overflow-x: auto; }
  .where { display: grid; grid-template-columns: max-content 1fr; gap: 8px 18px; margin: 0; font-size: 13px; }
  .where dt { color: var(--text-2); }
  .where dd { margin: 0; overflow-wrap: anywhere; }
  .where dd.mono { font-size: 12.5px; }
  @media (max-width: 600px) { .where { grid-template-columns: 1fr; gap: 2px; } .where dd { margin-bottom: 8px; } }
</style>
