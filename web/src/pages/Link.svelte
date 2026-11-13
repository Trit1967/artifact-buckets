<script>
  // "Connect Claude Code on this computer". Setup on the computer (the one-line installer, or Claude Code
  // following setup.md) opens this page. One press here both lets Claude Code sign in and, if the person wants,
  // copies the work Claude already made there. They pick which folders; nothing is asked in the terminal.
  import { api } from '../lib/api.js';
  import { session } from '../lib/session.svelte.js';
  import { route } from '../lib/router.svelte.js';
  import { bytes } from '../lib/format.js';
  import Logo from '../lib/Logo.svelte';
  import Icon from '../lib/Icon.svelte';

  const code = (route.query.get('code') || '').trim().toUpperCase();
  let info = $state(null);
  let error = $state(code ? '' : 'missing');
  let busy = $state(false);
  let outcome = $state(null);   // 'approved' | 'denied'
  let synced = $state(false);
  let keep = $state(new Set());
  const home = `/users/${session.user.username}`;

  if (code) {
    api.get(`/api/sync/link/${encodeURIComponent(code)}`, null, { quiet: true })
      .then((d) => { info = d; keep = new Set((d.folders || []).map((f) => f.folder)); if (d.status !== 'pending') error = d.status === 'denied' ? 'used' : 'connected'; })
      .catch((e) => { error = e.status === 404 ? 'expired' : 'other'; });
  }

  const folders = $derived(info?.folders || []);
  const chosen = $derived(folders.filter((f) => keep.has(f.folder)));
  const nFiles = $derived(chosen.reduce((a, f) => a + f.files, 0));
  const nBytes = $derived(chosen.reduce((a, f) => a + f.bytes, 0));
  const allOn = $derived(folders.length > 0 && chosen.length === folders.length);
  const label = (f) => f.label || (f.folder === 'inbox' ? 'Loose files' : f.folder.slice('projects/'.length));
  // The end of a path is the part people recognise.
  const tail = (p) => (p.length > 34 ? '…' + p.slice(-33) : p);
  const dest = (f) => `${home}/${f.folder}`;

  function toggle(f) { const k = new Set(keep); k.has(f.folder) ? k.delete(f.folder) : k.add(f.folder); keep = k; }
  function toggleAll() { keep = allOn ? new Set() : new Set(folders.map((f) => f.folder)); }

  async function decide(d) {
    busy = true;
    try {
      const r = await api.post(`/api/sync/link/${encodeURIComponent(code)}/${d}`, d === 'allow' ? { keep: [...keep] } : {});
      outcome = r.status; synced = !!r.sync;
    } catch (e) { error = e.status === 404 ? 'expired' : e.status === 400 ? 'used' : 'other'; }
    finally { busy = false; }
  }
  const chars = $derived((info?.code || code).split(''));
  const files = (n) => `${n.toLocaleString()} file${n === 1 ? '' : 's'}`;
</script>

<div class="wrap">
  <div class="card box">
    {#if error}
      <span class="tile"><Logo size={22} /></span>
      <div class="txt">
        <h1>{error === 'connected' ? `${info?.computer || 'This computer'} is connected` : error === 'expired' ? 'This link has expired' : error === 'used' ? 'This link was already used' : error === 'missing' ? 'There’s no code in this link' : 'Something went wrong'}</h1>
        <p class="muted">{error === 'connected' ? 'This link has done its job. You can close this tab.' : 'Links last ten minutes and work once. On the computer, run setup again and it opens a new one.'}</p>
      </div>
      <a class="btn" href="/connect" style="align-self:flex-start">{error === 'connected' ? 'See connected computers' : 'Connect Claude Code'}</a>
    {:else if !info}
      <span class="tile"><Logo size={22} /></span>
      <p class="muted" style="margin:0">Checking the link…</p>
    {:else if outcome === 'approved'}
      <div class="row" style="gap:14px">
        <span class="tile"><Logo size={22} /></span>
        <span class="dash done"></span>
        <span class="tile pc ok"><Icon name="check" size={20} /></span>
      </div>
      <div class="txt">
        <h1>Claude Code on <span class="name">{info.computer}</span> is connected</h1>
        {#if synced && nFiles > 0}
          <p class="muted">It’s copying {files(nFiles)} into your folders now, and adds new ones after each Claude Code reply. Go back to the computer; setup finishes by itself.</p>
        {:else if synced}
          <p class="muted">From now on, files Claude makes there are copied into your folders after each reply. Go back to the computer; setup finishes by itself.</p>
        {:else}
          <p class="muted">Nothing will be copied from it. Go back to the computer; setup finishes by itself. You can turn on syncing later from Connect Claude Code.</p>
        {/if}
      </div>
      <div class="row" style="gap:10px">
        {#if synced}<a class="btn pri" href={session.welcome ? '/welcome?step=work' : '/connect#sync'}>Watch them arrive</a>{/if}
        <a class={synced ? 'btn' : 'btn pri'} href={`/files?path=${encodeURIComponent(home)}`}>Open my folders</a>
      </div>
    {:else if outcome === 'denied'}
      <span class="tile"><Logo size={22} /></span>
      <div class="txt">
        <h1>Not connected</h1>
        <p class="muted"><span class="name">{info.computer}</span> wasn’t connected and nothing was copied. You can close this tab.</p>
      </div>
    {:else}
      <div class="row" style="gap:14px">
        <span class="tile"><Logo size={22} /></span>
        <span class="dash"></span>
        <span class="tile pc"><Icon name="laptop" size={20} /></span>
      </div>
      <div class="txt">
        <h1>Connect Claude Code on <span class="name">{info.computer}</span>?</h1>
        <p class="muted">Signed in as <b>{session.user.display_name}</b>. Claude Code there will be able to save to your folders and open what you can open.</p>
      </div>
      {#if !info.same_network}
        <div class="foreign"><Icon name="alert" /><span>This request came from another network address{info.from_ip ? ` (${info.from_ip})` : ''}, not from this browser’s. Connect only if you started setup yourself, on your own computer, just now. The computer shows this code:</span></div>
        <div class="code mono" aria-label={`Code ${info.code}`}>
          {#each chars as ch}{#if ch === '-'}<span class="sep"></span>{:else}<span class="ch">{ch}</span>{/if}{/each}
        </div>
      {/if}

      <section class="bring">
        {#if folders.length}
          <div class="bh">
            <div>
              <h2>Bring in your existing work</h2>
              <p class="muted">Files Claude made on this computer that aren’t in git. Untick anything that shouldn’t leave it, like a client’s work.</p>
            </div>
            <button class="all" type="button" onclick={toggleAll}>{allOn ? 'Untick all' : 'Tick all'}</button>
          </div>
          <ul class="fl" role="list">
            {#each folders as f (f.folder)}
              <li>
                <label class:off={!keep.has(f.folder)}>
                  <input type="checkbox" checked={keep.has(f.folder)} onchange={() => toggle(f)} />
                  <span class="box2" aria-hidden="true"><Icon name="check" size={12} /></span>
                  <span class="fi"><Icon name={f.folder === 'inbox' ? 'inbox' : 'folder'} size={15} /></span>
                  <span class="fn">
                    <span class="nm">{label(f)}</span>
                    {#if f.source}<span class="src mono" title={f.source}>{tail(f.source)}</span>{:else}<span class="src">Files from outside a project</span>{/if}
                  </span>
                  <span class="ct">{files(f.files)}<span class="sz">{bytes(f.bytes)}</span></span>
                </label>
              </li>
            {/each}
          </ul>
          {#if info.hidden}<p class="note warnline"><Icon name="info" size={13} /> {info.hidden} more {info.hidden === 1 ? 'folder' : 'folders'} with unusual names {info.hidden === 1 ? 'isn’t' : 'aren’t'} listed, and won’t be copied.</p>{/if}
          <p class="note">Copies go to <span class="mono">{home}/projects</span> and <span class="mono">inbox</span>, then new files follow after each Claude Code reply. Secrets, build folders and anything in git are always left out.</p>
        {:else}
          <div class="bh"><div>
            <h2>Keep Claude’s files in sync</h2>
            <p class="muted">There’s nothing to bring in yet. From now on, files Claude makes on this computer are copied to <span class="mono">{home}/projects</span> and <span class="mono">inbox</span> after each reply. Secrets, build folders and anything in git are left out.</p>
          </div></div>
        {/if}
      </section>

      <p class="note" style="margin:0"><Icon name="lock" size={13} /> The computer can only add files to your own folders. It can’t open, change, delete or share anything already here, and you can turn it off at any time under Connect Claude Code.</p>

      <div class="acts">
        <button class="btn" disabled={busy} onclick={() => decide('connect')}>Connect without syncing</button>
        <button class="btn pri" disabled={busy} onclick={() => decide('allow')}>
          {#if folders.length && nFiles}Connect and copy {files(nFiles)}{:else}Connect and sync{/if}
        </button>
      </div>
      <div class="foot">
        <button class="linkish" disabled={busy} onclick={() => decide('deny')}>I didn’t start this</button>
        {#if folders.length && nFiles}<span class="tot faint">{chosen.length} of {folders.length} folders · {bytes(nBytes)}</span>{/if}
      </div>
    {/if}
  </div>
</div>

<style>
  .wrap { min-height: 100vh; display: grid; place-items: center; padding: 32px 16px; background: radial-gradient(900px 500px at 50% 0%, #17130C 0%, transparent 70%), var(--bg); }
  .box { width: 100%; max-width: 560px; padding: 30px; display: flex; flex-direction: column; gap: 22px; }
  .txt { display: flex; flex-direction: column; gap: 8px; }
  .txt h1 { font-size: 22px; margin: 0; line-height: 1.3; }
  .txt p { margin: 0; font-size: 13px; line-height: 1.55; }
  .txt b { color: var(--text); font-weight: 500; }
  .name { color: var(--text); overflow-wrap: anywhere; }
  .tile { width: 46px; height: 46px; border-radius: 12px; background: #1A1B1F; border: 1px solid var(--line-strong); display: grid; place-items: center; flex: none; }
  .tile.pc { color: var(--text-2); }
  .tile.ok { color: var(--green); border-color: #2C4A3A; background: var(--green-bg); }
  .dash { flex: 0 0 40px; border-top: 1.5px dashed var(--line-hover); }
  .dash.done { border-top-style: solid; border-color: #2C4A3A; }
  .code { display: flex; align-items: center; gap: 6px; padding: 16px; border-radius: 12px; background: #0F1012; border: 1px solid var(--line); justify-content: center; }
  .ch { width: 36px; height: 46px; border-radius: 8px; background: var(--raise); border: 1px solid var(--line-strong); display: grid; place-items: center; font-size: 22px; font-weight: 500; color: var(--amber-hi); }
  .sep { width: 10px; height: 2px; border-radius: 2px; background: var(--line-hover); margin: 0 4px; }
  .foreign { display: flex; gap: 10px; align-items: flex-start; padding: 12px 14px; border-radius: 10px; background: var(--red-bg); border: 1px solid #5A2E24; color: var(--text-2); font-size: 13px; line-height: 1.5; }
  .foreign :global(.ic) { color: var(--red); flex: none; margin-top: 1px; }
  .bring { display: flex; flex-direction: column; gap: 12px; }
  .bh { display: flex; gap: 16px; align-items: flex-start; justify-content: space-between; }
  .bh h2 { font-size: 14px; font-weight: 500; margin: 0 0 4px; color: var(--text); }
  .bh p { margin: 0; font-size: 13px; line-height: 1.5; }
  .all { background: none; border: 0; color: var(--amber-hi); font: inherit; font-size: 12.5px; cursor: pointer; padding: 2px 0; white-space: nowrap; }
  .all:hover { text-decoration: underline; }
  .fl { list-style: none; margin: 0; padding: 4px; border-radius: 12px; background: #0F1012; border: 1px solid var(--line); max-height: 300px; overflow: auto; }
  .fl label { display: grid; grid-template-columns: 18px 22px minmax(0, 1fr) auto; gap: 10px; align-items: center; padding: 9px 10px; border-radius: 8px; cursor: pointer; position: relative; }
  .fl label:hover { background: var(--raise); }
  .fl input { position: absolute; opacity: 0; width: 1px; height: 1px; }
  .box2 { width: 18px; height: 18px; border-radius: 5px; border: 1.5px solid var(--amber); background: var(--amber); color: var(--amber-ink); display: grid; place-items: center; transition: background .12s, border-color .12s; }
  .off .box2 { background: transparent; border-color: var(--line-hover); color: transparent; }
  .fl input:focus-visible + .box2 { outline: 2px solid var(--amber-hi); outline-offset: 2px; }
  .fi { color: var(--amber); display: grid; place-items: center; }
  .off .fi { color: var(--faint); }
  .fn { display: flex; flex-direction: column; min-width: 0; gap: 2px; }
  .nm { font-size: 13.5px; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .off .nm { color: var(--text-2); text-decoration: line-through; text-decoration-color: var(--line-hover); }
  .src { font-size: 11.5px; color: var(--faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ct { font-size: 12.5px; color: var(--text-2); text-align: right; display: flex; flex-direction: column; gap: 2px; font-variant-numeric: tabular-nums; }
  .ct .sz { font-size: 11.5px; color: var(--faint); }
  .off .ct { color: var(--faint); }
  .note :global(.ic) { vertical-align: -2px; color: var(--faint); }
  .acts { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; justify-content: flex-end; }
  .foot { display: flex; align-items: center; gap: 10px; justify-content: space-between; margin-top: -10px; }
  .linkish { background: none; border: 0; padding: 0; color: var(--faint); font: inherit; font-size: 12.5px; cursor: pointer; }
  .linkish:hover { color: var(--red); text-decoration: underline; }
  .tot { font-size: 12px; font-variant-numeric: tabular-nums; }
  .warnline { margin: 0; }
  @media (max-width: 520px) {
    .acts { flex-direction: column-reverse; align-items: stretch; }
    .acts .btn { justify-content: center; }
    .foot { flex-direction: column-reverse; gap: 8px; margin-top: -4px; }
  }
  @media (max-width: 420px) {
    .box { padding: 22px; }
    .code { gap: 4px; padding: 12px 8px; }
    .ch { width: 30px; height: 40px; font-size: 18px; }
    .sep { margin: 0 2px; }
  }
</style>
