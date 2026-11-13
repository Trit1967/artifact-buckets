<script>
  // First-run welcome: what this is, connect Claude Code, bring in existing work, where things land.
  // Shown once after a person's first sign-in; "Getting started" in the account menu opens it again.
  import { api } from '../lib/api.js';
  import { session } from '../lib/session.svelte.js';
  import { route, navigate } from '../lib/router.svelte.js';
  import { ago, agoPhrase, bytes } from '../lib/format.js';
  import Logo from '../lib/Logo.svelte';
  import Icon from '../lib/Icon.svelte';
  import Copy from '../lib/Copy.svelte';

  const STEPS = [
    { key: 'hello', label: 'Welcome' },
    { key: 'connect', label: 'Connect Claude Code' },
    { key: 'work', label: 'Bring in your work' },
    { key: 'land', label: 'Where things land' },
  ];
  let step = $state(Math.max(0, STEPS.findIndex((s) => s.key === route.query.get('step'))));
  const first = (session.user.display_name || session.user.username).split(' ')[0];
  const home = `/users/${session.user.username}`;
  const setup = session.claude || {};
  let os = $state(/Windows/i.test(navigator.userAgent) ? 'windows' : 'unix');

  // Live state: Claude Code sign-ins and synced computers, refreshed while the page is open.
  let devices = $state(null);
  let sync = $state(null);
  async function refresh() {
    try {
      const [t, s] = await Promise.all([api.get('/api/tokens', null, { quiet: true }), api.get('/api/sync/status', null, { quiet: true })]);
      devices = t.tokens.filter((x) => x.kind === 'device');
      sync = s;
    } catch { /* keep the last good state */ }
  }
  refresh();
  $effect(() => { const id = setInterval(refresh, 3000); return () => clearInterval(id); });

  const connected = $derived(devices?.length > 0);
  const linked = $derived(sync?.computers?.length > 0);
  const folders = $derived(sync?.folders || []);
  const projectFolders = $derived(folders.filter((f) => f.folder.startsWith(`${home}/projects/`)));
  const inbox = $derived(folders.find((f) => f.folder === `${home}/inbox`));

  // A step shows a check when it's actually done, not just passed.
  const done = $derived([step > 0, connected, linked, false]);
  function go(i) { step = i; navigate(`/welcome?step=${STEPS[i].key}`, true); }
  async function finish(to = '/files') {
    await api.post('/api/sync/welcome/done').catch(() => {});
    session.welcome = false;
    navigate(to, true);
  }
  const short = (p) => p.slice(home.length + 1);
</script>

<div class="wrap">
  <header class="top">
    <span class="brand"><Logo /><span>Artifact Buckets</span></span>
    <button class="skip" onclick={() => finish()}>{step === STEPS.length - 1 ? 'Close' : 'Skip for now'}</button>
  </header>

  <div class="frame">
    <ol class="rail" aria-label="Steps">
      {#each STEPS as s, i}
        <li>
          <button class="st" class:on={i === step} class:done={i !== step && done[i]} aria-current={i === step ? 'step' : undefined} onclick={() => go(i)}>
            <span class="dot mono">{#if i !== step && done[i]}<Icon name="check" size={13} />{:else}{i + 1}{/if}</span>
            <span class="lab">{s.label}</span>
            {#if s.key === 'connect' && connected}<span class="pip" title="Connected"></span>{/if}
            {#if s.key === 'work' && linked}<span class="pip" title="Syncing"></span>{/if}
          </button>
        </li>
      {/each}
    </ol>

    <section class="card panel">
      {#key step}
      <div class="body">
        {#if step === 0}
          <p class="eyebrow">Hello, {first}</p>
          <h1>Everything Claude makes, kept in one place.</h1>
          <p class="lede">Reports, diagrams, scripts and notes from Claude Code land in your folders here, filed by project. You can find them, open them in the browser, and share them with your team.</p>
          <div class="trio">
            <div class="tri"><span class="ti"><Icon name="home" /></span><b>Your folder</b><span class="mono">{home}</span></div>
            <div class="tri"><span class="ti"><Icon name="terminal" /></span><b>Claude Code</b><span>saves here directly, once connected</span></div>
            <div class="tri"><span class="ti"><Icon name="share" /></span><b>Your team</b><span>sees what you choose to share</span></div>
          </div>
          <p class="note">This takes about two minutes. You can skip any step and come back from <b>Getting started</b> in your account menu.</p>

        {:else if step === 1}
          <p class="eyebrow">Step 2 of 4</p>
          <h1>Connect Claude Code</h1>
          <p class="lede">One line installs the Artifact Buckets plugin, already pointed at this server, then opens this site so you can sign in. Nothing to configure.</p>
          <div class="segs os" role="group" aria-label="Your computer">
            <button class="seg" class:on={os === 'windows'} onclick={() => (os = 'windows')}>Windows</button>
            <button class="seg" class:on={os === 'unix'} onclick={() => (os = 'unix')}>macOS and Linux</button>
          </div>
          <Copy text={os === 'windows' ? setup.windows : setup.unix} label="the setup command" />
          <p class="note">Paste it into {os === 'windows' ? 'PowerShell' : 'a terminal'}.{#if setup.private_ca}{' '}This lab server has its own certificate, so {os === 'windows' ? 'Windows' : 'your computer'} asks you to trust it once.{/if} Or paste this into Claude Code and it does the steps for you:</p>
          <Copy text={setup.prompt} label="the prompt" />
          <div class="live" class:ok={connected}>
            {#if connected}
              <span class="lv-i"><Icon name="check" /></span>
              <span><b>Connected.</b> {devices[0].name}, {agoPhrase(devices[0].created_at)}.{#if devices.length > 1} And {devices.length - 1} more.{/if}</span>
            {:else}
              <span class="pulse" aria-hidden="true"></span>
              <span>Waiting for Claude Code to sign in… this updates by itself.</span>
            {/if}
          </div>

        {:else if step === 2}
          <p class="eyebrow">Step 3 of 4</p>
          <h1>Bring in what Claude already made</h1>
          <p class="lede">Claude Code remembers the files it created and edited on your computer. The setup line looks through that history, shows you what it found, and asks before copying anything. After that, new files arrive by themselves after each reply.</p>
          <ul class="rules">
            <li><Icon name="check" /><span>Files Claude created or edited, filed by project</span></li>
            <li class="no"><Icon name="x" /><span>Files already kept in git, secrets like <span class="mono">.env</span> and keys, build and dependency folders</span></li>
            <li class="no"><Icon name="x" /><span>It can only add files to your folders. It can’t open, change or delete anything here</span></li>
          </ul>
          {#if !linked}
            <p class="note">Already ran the setup line and said no? Run it again; it skips what’s done and asks once more.</p>
            <Copy text={os === 'windows' ? setup.windows : setup.unix} label="the setup command" />
          {/if}
          <div class="arrivals">
            <div class="ar-hd">
              {#if linked}
                <span class="pip big"></span>
                <span class="ar-who"><b>{sync.computers.map((c) => c.name).join(', ')}</b> {sync.computers.length > 1 ? 'are' : 'is'} syncing{#if sync.computers[0].last_sync}, last copied {agoPhrase(sync.computers[0].last_sync)}{/if}</span>
                <span class="mono count">{sync.files} {sync.files === 1 ? 'file' : 'files'} · {bytes(sync.bytes, 1)}</span>
              {:else}
                <span class="pulse" aria-hidden="true"></span><span>Waiting for a computer to link… files appear here as they arrive.</span>
              {/if}
            </div>
            {#if sync?.recent?.length}
              <ul class="feed">
                {#each sync.recent.slice(0, 6) as f (f.path + f.version)}
                  <li title={short(f.path)}><Icon name="doc" /><span class="mono fp"><span class="fdir">{short(f.path).replace(/[^/]*$/, '')}</span><span class="fname">{f.path.split('/').pop()}</span></span><span class="note when">{ago(f.at)}</span></li>
                {/each}
              </ul>
            {/if}
          </div>

        {:else}
          <p class="eyebrow">Step 4 of 4</p>
          <h1>Where things land</h1>
          <p class="lede">Each Claude Code project gets its own folder, named after its repository or the folder you ran Claude in. Anything made outside a project goes to your inbox.</p>
          <div class="tree mono">
            <div class="tr root"><Icon name="home" /><span>{home}</span></div>
            <div class="tr l1"><Icon name="folder" /><span>projects</span></div>
            {#each (projectFolders.length ? projectFolders.slice(0, 4) : [{ folder: `${home}/projects/your-repo`, files: 0, sample: true }]) as f}
              <div class="tr l2" class:sample={f.sample}><Icon name="folder" /><span>{f.folder.split('/').pop()}</span>{#if f.files}<span class="cnt">{f.files} {f.files === 1 ? 'file' : 'files'}</span>{/if}</div>
            {/each}
            {#if projectFolders.length > 4}<div class="tr l2 sample"><span>and {projectFolders.length - 4} more</span></div>{/if}
            <div class="tr l1"><Icon name="inbox" /><span>inbox</span>{#if inbox}<span class="cnt">{inbox.files} {inbox.files === 1 ? 'file' : 'files'}</span>{/if}</div>
          </div>
          <p class="note">When your team keeps a repository’s work in a team folder, an admin can set a rule and Claude saves there instead. Things to try in Claude Code:</p>
          <ul class="eg">
            <li>“Save this report to my buckets”</li>
            <li>“Find the diagrams I made last week”</li>
            <li>“Share the q3 folder with finance as viewers”</li>
          </ul>
        {/if}
      </div>
      {/key}

      <footer class="foot">
        {#if step > 0}<button class="btn" onclick={() => go(step - 1)}>Back</button>{:else}<span></span>{/if}
        {#if step < STEPS.length - 1}
          {@const later = (step === 1 && !connected) || (step === 2 && !linked)}
          <button class="btn" class:pri={!later} onclick={() => go(step + 1)}>
            {step === 0 ? 'Get started' : later ? 'Skip for now' : 'Continue'}
          </button>
        {:else}
          <button class="btn pri" onclick={() => finish(projectFolders.length ? `/files?path=${encodeURIComponent(home + '/projects')}` : '/files')}>Go to my files</button>
        {/if}
      </footer>
    </section>
  </div>
</div>

<style>
  .wrap { min-height: 100vh; display: flex; flex-direction: column; background: radial-gradient(1100px 600px at 70% -10%, #17130C 0%, transparent 65%), var(--bg); }
  .top { display: flex; align-items: center; justify-content: space-between; padding: 20px 28px; }
  .brand { display: flex; align-items: center; gap: 10px; font-size: 15px; font-weight: 600; letter-spacing: -0.01em; }
  .skip { all: unset; cursor: pointer; font-size: 13px; color: var(--muted); padding: 6px 10px; border-radius: 8px; }
  .skip:hover { color: var(--text); background: #16181B; }
  .skip:focus-visible, .st:focus-visible { outline: 2px solid var(--amber); outline-offset: 1px; }

  .frame { flex: 1; width: 100%; max-width: 1000px; margin: 0 auto; padding: 24px 28px 56px; display: grid; grid-template-columns: 220px 1fr; gap: 40px; align-items: start; }
  .rail { list-style: none; margin: 0; padding: 18px 0 0; display: flex; flex-direction: column; gap: 4px; position: relative; }
  .rail::before { content: ''; position: absolute; left: 22px; top: 34px; bottom: 16px; width: 1px; background: var(--line); }
  .st { all: unset; box-sizing: border-box; cursor: pointer; display: flex; align-items: center; gap: 12px; width: 100%; padding: 8px 10px; border-radius: 10px; color: var(--muted); font-size: 14px; position: relative; }
  .st:hover { color: var(--text); }
  .dot { width: 26px; height: 26px; border-radius: 50%; display: grid; place-items: center; font-size: 12px; background: var(--bg); border: 1px solid var(--line-strong); color: var(--faint); flex: none; }
  .st.on { color: var(--text); background: var(--card); box-shadow: inset 0 0 0 1px var(--line); }
  .st.on .dot { background: var(--amber); border-color: var(--amber); color: var(--amber-ink); font-weight: 600; }
  .st.done .dot { background: var(--green-bg); border-color: #2C4A3A; color: var(--green); }
  .lab { flex: 1; }
  .pip { width: 7px; height: 7px; border-radius: 50%; background: var(--green); box-shadow: 0 0 0 3px rgba(76, 195, 138, .15); flex: none; }
  .pip.big { width: 8px; height: 8px; }

  .panel { padding: 0; display: flex; flex-direction: column; min-height: 520px; overflow: hidden; }
  .body { padding: 36px 40px 28px; display: flex; flex-direction: column; gap: 18px; flex: 1; animation: rise .28s ease-out; }
  @keyframes rise { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
  .eyebrow { margin: 0; font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: var(--amber); font-weight: 500; }
  h1 { margin: -6px 0 0; font-size: 28px; line-height: 1.2; letter-spacing: -0.02em; max-width: 20ch; }
  .lede { margin: 0; font-size: 14.5px; line-height: 1.6; color: var(--text-2); max-width: 58ch; }
  .note { margin: 0; font-size: 13px; line-height: 1.55; color: var(--muted); }
  .note b { color: var(--text-2); font-weight: 500; }
  .os { align-self: flex-start; }

  .trio { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 6px 0; }
  .tri { padding: 16px; border-radius: 12px; background: var(--card-2); border: 1px solid var(--line); display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--muted); line-height: 1.45; min-width: 0; }
  .tri b { color: var(--text); font-weight: 500; font-size: 14px; }
  .tri .mono { font-size: 12px; overflow-wrap: anywhere; color: var(--text-2); }
  .ti { width: 32px; height: 32px; border-radius: 9px; background: var(--amber-bg); color: var(--amber); display: grid; place-items: center; margin-bottom: 4px; }

  .live { display: flex; align-items: center; gap: 12px; padding: 14px 16px; border-radius: 12px; border: 1px dashed var(--line-hover); font-size: 13px; color: var(--muted); }
  .live.ok { border-style: solid; border-color: #2C4A3A; background: var(--green-bg); color: var(--text-2); }
  .live b { color: var(--green); font-weight: 600; }
  .lv-i { width: 24px; height: 24px; border-radius: 50%; background: var(--green); color: #0B1A12; display: grid; place-items: center; flex: none; }
  .pulse { width: 8px; height: 8px; border-radius: 50%; background: var(--amber); flex: none; animation: pulse 1.6s ease-in-out infinite; }
  @keyframes pulse { 0%, 100% { box-shadow: 0 0 0 0 rgba(242, 169, 59, .45); } 50% { box-shadow: 0 0 0 6px rgba(242, 169, 59, 0); } }

  .rules { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 9px; font-size: 13px; line-height: 1.45; color: var(--text-2); }
  .rules li { display: flex; gap: 10px; align-items: flex-start; }
  .rules :global(.ic) { color: var(--green); flex: none; margin-top: 1px; }
  .rules .no :global(.ic) { color: var(--faint); }
  .rules .mono { font-size: 12px; }

  .arrivals { border-radius: 12px; border: 1px solid var(--line); background: #0F1012; overflow: hidden; }
  .ar-hd { display: flex; align-items: center; gap: 12px; padding: 14px 16px; font-size: 13px; color: var(--muted); flex-wrap: wrap; }
  .ar-hd b { color: var(--text); font-weight: 500; }
  .ar-who { flex: 1; min-width: 0; }
  .count { font-size: 12px; color: var(--text-2); white-space: nowrap; margin-left: auto; }
  .when { flex: none; white-space: nowrap; }
  .feed { list-style: none; margin: 0; padding: 0; border-top: 1px solid var(--line-soft); }
  .feed li { display: flex; align-items: center; gap: 10px; padding: 9px 16px; font-size: 12.5px; border-bottom: 1px solid var(--line-soft); animation: rise .3s ease-out; }
  .feed li:last-child { border-bottom: 0; }
  .feed :global(.ic) { color: var(--faint); flex: none; }
  .fp { flex: 1; min-width: 0; display: flex; white-space: nowrap; color: var(--text-2); }
  .fdir { min-width: 0; overflow: hidden; text-overflow: ellipsis; color: var(--faint); }
  .fname { flex: none; max-width: 100%; overflow: hidden; text-overflow: ellipsis; }

  .tree { padding: 16px 18px; border-radius: 12px; background: #0F1012; border: 1px solid var(--line); display: flex; flex-direction: column; gap: 2px; font-size: 13px; }
  .tr { display: flex; align-items: center; gap: 10px; height: 30px; color: var(--text-2); position: relative; }
  .tr :global(.ic) { color: var(--amber); flex: none; }
  .tr.root { color: var(--text); }
  .tr.l1 { padding-left: 22px; }
  .tr.l2 { padding-left: 46px; }
  .tr.l1::before, .tr.l2::before { content: ''; position: absolute; top: 15px; height: 1px; width: 12px; background: var(--line-hover); }
  .tr.l1::before { left: 6px; } .tr.l2::before { left: 30px; }
  .tr.sample { color: var(--faint); font-style: italic; }
  .tr.sample :global(.ic) { color: var(--faint); }
  .cnt { margin-left: auto; font-size: 11.5px; color: var(--faint); font-family: var(--sans); }
  .eg { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--text-2); }

  .foot { display: flex; justify-content: space-between; align-items: center; gap: 10px; padding: 16px 40px; border-top: 1px solid var(--line); background: var(--card-2); }

  @media (max-width: 820px) {
    .frame { grid-template-columns: 1fr; gap: 16px; padding: 8px 16px 32px; }
    .rail { flex-direction: row; padding: 0; gap: 6px; }
    .rail li { flex: 1; min-width: 0; }
    .rail::before { display: none; }
    .st { flex-direction: column; gap: 6px; padding: 8px 4px; font-size: 11.5px; text-align: center; }
    .st.on { background: none; box-shadow: none; }
    .st .pip { position: absolute; top: 6px; right: calc(50% - 20px); }
    .body { padding: 26px 20px 22px; }
    .foot { padding: 14px 20px; }
    h1 { font-size: 24px; }
    .trio { grid-template-columns: 1fr; }
    .top { padding: 16px; }
  }
</style>
