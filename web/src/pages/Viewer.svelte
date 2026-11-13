<script>
  import { api } from '../lib/api.js';
  import { session } from '../lib/session.svelte.js';
  import { route, navigate } from '../lib/router.svelte.js';
  import { toast, fail } from '../lib/toast.svelte.js';
  import { bytes, ago, when, crumbs, ext, extTint, SOURCE, prettyJson } from '../lib/format.js';
  import { markdown, csv } from '../lib/markdown.js';
  import Icon from '../lib/Icon.svelte';
  import Modal from '../lib/Modal.svelte';
  import RoleTag from '../lib/RoleTag.svelte';
  import ShareDialog from '../lib/ShareDialog.svelte';
  import Versions from '../lib/Versions.svelte';

  const me = session.user.username;
  const home = `/users/${me}`;
  const RANK = { viewer: 1, editor: 2, owner: 3 };
  const atLeast = (r, need) => !!r && RANK[r] >= RANK[need];

  let path = $derived(route.query.get('path') || '');
  let rev = $state(0);
  let f = $state(null);
  let error = $state('');
  let text = $state(null);
  let versions = $state(null);
  let mode = $state('view');   // view | source (for Markdown, HTML, CSV)
  let dialog = $state(null);
  let nameInput = $state('');
  let destInput = $state('');

  const MAX_TEXT = 2 * 1024 * 1024;
  const kind = (m) => {
    const e = ext(m.name);
    if (/^text\/html/.test(m.mime) || e === 'html' || e === 'htm') return 'html';
    if (m.mime === 'image/svg+xml') return 'svg';
    if (m.mime === 'application/pdf') return 'pdf';
    if (/^image\/(png|jpe?g|gif|webp)/.test(m.mime)) return 'image';
    if (e === 'md' || e === 'markdown' || m.mime === 'text/markdown') return 'md';
    if (e === 'csv' || m.mime === 'text/csv') return 'csv';
    if (e === 'json' || m.mime === 'application/json') return 'json';
    if (/^(text\/|application\/(x-yaml|yaml|x-sh|xml|javascript|x-python))/.test(m.mime) ||
        /^(txt|log|ya?ml|toml|ini|cfg|py|js|ts|tsx|jsx|sql|sh|ps1|go|rs|java|c|h|cpp|cs|rb|php|css|scss|xml|tf|hcl|dockerfile)$/.test(e)) return 'code';
    return 'other';
  };

  $effect(() => {
    const p = path; rev;
    f = null; text = null; versions = null; error = '';
    if (!p) { error = 'No file was given.'; return; }
    api.get('/api/fs/meta', { path: p }).then(async (m) => {
      f = m;
      const k = kind(m);
      if (['md', 'csv', 'json', 'code', 'html'].includes(k) && m.size <= MAX_TEXT) {
        const r = await fetch(api.download(p), { credentials: 'same-origin' });
        if (r.ok) text = await r.text();
      }
      api.get('/api/fs/versions', { path: p }).then((v) => (versions = v.versions)).catch(() => {});
    }).catch((e) => {
      error = e.status === 404 ? 'This file doesn’t exist, or you don’t have access to it.' : e.message;
    });
  });

  const k = $derived(f ? kind(f) : null);
  $effect(() => {
    const name = path ? path.split('/').pop() : '';
    document.title = name ? `${name} · Artifact Buckets` : 'Artifact Buckets';
    return () => { document.title = 'Artifact Buckets'; };
  });
  const folder = $derived(path.split('/').slice(0, -1).join('/'));
  const canEdit = $derived(atLeast(f?.role, 'editor'));
  const canShare = $derived(atLeast(f?.role, 'owner'));
  const trail = $derived(crumbs(folder).filter((c) => c.path !== '/users' && c.path !== '/shared')
    .map((c) => ({ ...c, name: c.path === home ? 'Home' : c.name })));
  const lines = $derived(text === null ? [] : (k === 'json' && mode === 'view' ? prettyJson(text) : text).split('\n'));
  const table = $derived(k === 'csv' && text !== null ? csv(text) : null);
  const numCols = $derived(table ? (table[0] || []).map((_, j) => table.slice(1).every((r) => r[j] === undefined || r[j] === '' || /^-?[\d.,]+%?$/.test(r[j].trim()))) : []);
  const tint = $derived(f ? extTint(f.name) : ['', '']);
  const src = $derived(f ? (SOURCE[f.source] || [f.source, '#9097A0']) : ['', '']);


  async function rename(name) {
    const dst = folder + '/' + name;
    try { await api.post('/api/fs/move', { src: path, dst }); dialog = null; toast('Renamed'); navigate(`/view?path=${encodeURIComponent(dst)}`, true); }
    catch (e) { fail(e); }
  }
  async function move(dest) {
    const dst = dest.replace(/\/$/, '') + '/' + f.name;
    try { await api.post('/api/fs/move', { src: path, dst }); dialog = null; toast(`Moved to ${dest}`); navigate(`/view?path=${encodeURIComponent(dst)}`, true); }
    catch (e) { fail(e); }
  }
  async function remove() {
    try {
      await api.post('/api/fs/delete', { path });
      dialog = null; toast(`Moved to trash. You can restore it for ${session.trash_days || 30} days.`);
      navigate(`/files?path=${encodeURIComponent(folder)}`, true);
    } catch (e) { fail(e); }
  }
  async function toClaude() {
    const cmd = `/buckets-open ${path}`;
    try { await navigator.clipboard.writeText(cmd); toast('Copied. Paste it into Claude Code to pull this file in and edit it.'); }
    catch { dialog = { kind: 'claude', cmd }; }
  }
  function onKey(e) {
    if (e.key === 'Escape' && !dialog && !e.target.closest?.('input,textarea')) navigate(`/files?path=${encodeURIComponent(folder)}&file=${encodeURIComponent(path)}`);
  }
</script>

<svelte:window onkeydown={onKey} />

<div class="page viewer">
  <header class="vhead">
    <a class="btn icon" href={`/files?path=${encodeURIComponent(folder)}&file=${encodeURIComponent(path)}`} aria-label="Back to folder" title="Back to folder (Esc)"><Icon name="back" /></a>
    <div class="vtitle">
      <nav aria-label="Folder" class="trail">
        {#each trail as c, i}
          <a href={`/files?path=${encodeURIComponent(c.path)}`}>{c.name}</a>{#if i < trail.length - 1}<Icon name="chevR" size={12} style="color:var(--faint)" />{/if}
        {/each}
      </nav>
      <div class="row" style="gap:12px;min-width:0">
        {#if f}<span class="ext mono" style="background:{tint[0]};color:{tint[1]}">{ext(f.name) || 'file'}</span>{/if}
        <h1 class="fname" title={f?.name || path.split('/').pop()}>{f?.name || path.split('/').pop()}</h1>
        {#if f}<span class="tag mono">v{f.version}</span>{#if f.role}<RoleTag role={f.role} />{/if}{/if}
      </div>
    </div>
    {#if f}
      <div class="row wrap" style="gap:8px;justify-content:flex-end">
        {#if ['md', 'csv', 'html', 'json'].includes(k) && text !== null}
          <div class="segs" role="group" aria-label="View as">
            <button class="seg" class:on={mode === 'view'} onclick={() => (mode = 'view')}>{k === 'json' ? 'Formatted' : 'Preview'}</button>
            <button class="seg" class:on={mode === 'source'} onclick={() => (mode = 'source')}>Source</button>
          </div>
        {/if}
        {#if ['html', 'svg', 'pdf', 'image'].includes(k)}<a class="btn" data-native href={f.preview_url} target="_blank" rel="noopener"><Icon name="external" />Open in new tab</a>{/if}
        <button class="btn" onclick={toClaude} title="Copies a command that pulls this file into Claude Code"><Icon name="terminal" />Open in Claude Code</button>
        <button class="btn" onclick={() => (dialog = { kind: 'share' })}><Icon name="users" />{canShare ? 'Share' : 'Who has access'}</button>
        <a class="btn pri" data-native href={api.download(f.path)} download><Icon name="download" />Download</a>
      </div>
    {/if}
  </header>

  {#if error}
    <div class="card empty" style="padding:48px">{error}<br /><a href="/files">Back to your files</a></div>
  {:else if !f}
    <div class="card empty" style="padding:48px">Loading…</div>
  {:else}
    <div class="vbody">
      <section class="card stage" class:paper={(k === 'md' && mode === 'view')} class:fixed={['html', 'svg', 'pdf'].includes(k) && mode === 'view' || k === 'image' || k === 'other'} aria-label="File contents">
        {#if (k === 'html' || k === 'svg') && mode === 'view'}
          <iframe title={f.name} src={f.preview_url} sandbox="allow-scripts" referrerpolicy="no-referrer"></iframe>
        {:else if k === 'pdf'}
          <iframe title={f.name} src={f.preview_url} referrerpolicy="no-referrer"></iframe>
        {:else if k === 'image'}
          <div class="imgwrap"><img alt={f.name} src={f.preview_url} /></div>
        {:else if k === 'md' && mode === 'view' && text !== null}
          <article class="md">{@html markdown(text)}</article>
        {:else if k === 'csv' && mode === 'view' && table}
          <div class="csvwrap"><table class="csvt">
            <thead><tr><th class="ln"></th>{#each table[0] || [] as h, j}<th class:num={numCols[j]}>{h}</th>{/each}</tr></thead>
            <tbody>{#each table.slice(1) as r, i}<tr><td class="ln">{i + 1}</td>{#each r as c, j}<td class:num={numCols[j]}>{c}</td>{/each}</tr>{/each}</tbody>
          </table>{#if table.length >= 500}<p class="note" style="padding:12px 16px">Showing the first 500 rows. Download for the rest.</p>{/if}</div>
        {:else if text !== null}
          <div class="codeview"><table><tbody>{#each lines as l, i}<tr><td class="ln">{i + 1}</td><td class="lc">{l || ' '}</td></tr>{/each}</tbody></table></div>
        {:else}
          <div class="ph">
            <span class="big mono" style="color:{tint[1]}">.{ext(f.name) || 'file'}</span>
            <span class="muted" style="font-size:13px">{f.size > MAX_TEXT && k !== 'other' ? 'This file is too large to show here.' : 'There’s no preview for this kind of file.'}</span>
            <a class="btn pri" data-native href={api.download(f.path)} download><Icon name="download" />Download {bytes(f.size)}</a>
          </div>
        {/if}
      </section>

      <aside class="card rail" aria-label="About this file">
        <div class="kv"><span>Size</span><span class="mono">{bytes(f.size)}</span></div>
        <div class="kv"><span>Saved</span><span>{when(f.updated_at)}</span></div>
        <div class="kv"><span>Saved by</span><span>{f.saved_by || '—'}</span></div>
        <div class="kv"><span>From</span><span class="row" style="gap:8px"><span class="dot" style="width:6px;height:6px;background:{src[1]}"></span>{src[0]}</span></div>
        {#if f.project}<div class="kv"><span>Project</span><span class="mono">{f.project}</span></div>{/if}
        {#if f.git_remote}<div class="kv"><span>Repository</span><span class="mono brk">{f.git_remote}</span></div>{/if}
        {#if f.git_commit}<div class="kv"><span>Commit</span><span class="mono">{f.git_commit.slice(0, 10)}</span></div>{/if}
        {#if f.tags?.length}<div class="kv"><span>Tags</span><span class="tagrow">{#each f.tags as tg}<span class="tag">{tg}</span>{/each}</span></div>{/if}
        <div class="kv"><span>Folder</span><a class="brk" href={`/files?path=${encodeURIComponent(folder)}`}>{#each trail as c, i}{#if i} / <wbr />{/if}<span class="crumb">{c.name}</span>{/each}</a></div>

        <div class="vers">
          <div class="row" style="justify-content:space-between"><span class="sec">History</span>
            {#if versions && versions.length > 1}<button class="lnk" onclick={() => (dialog = { kind: 'versions' })}>All versions</button>{/if}</div>
          {#if versions}
            {#each versions.slice(0, 5) as v}
              <div class="ver" class:cur={v.current}>
                <span class="vdot"></span>
                <span class="grow" style="display:flex;flex-direction:column;gap:2px;min-width:0">
                  <span style="font-size:12.5px"><span class="mono">v{v.version}</span>{v.current ? ' · current' : ''}</span>
                  <span class="note ell">{v.saved_by || 'Unknown'} · {ago(v.saved_at)}</span>
                </span>
                <span class="note mono">{bytes(v.size)}</span>
              </div>
            {/each}
          {/if}
        </div>

        {#if canEdit}
          <div class="acts">
            <button class="btn sm" onclick={() => { nameInput = f.name; dialog = { kind: 'rename' }; }}><Icon name="edit" size={14} />Rename</button>
            <button class="btn sm" onclick={() => { destInput = folder; dialog = { kind: 'move' }; }}><Icon name="move" size={14} />Move</button>
            <button class="btn sm danger" onclick={() => (dialog = { kind: 'delete' })}><Icon name="trash" size={14} />Delete</button>
          </div>
        {/if}
        <div class="kv" style="border:0"><span>SHA-256</span><span class="mono brk" style="color:var(--faint);font-size:11px">{f.sha256}</span></div>
      </aside>
    </div>
  {/if}
</div>

{#if dialog?.kind === 'claude'}
  <Modal title="Open in Claude Code" onclose={() => (dialog = null)}>
    <p class="note" style="margin:0">Run this in Claude Code. It pulls the file into your session, and saves your changes back here as a new version.</p>
    <code class="mono" style="display:block;padding:12px 14px;border-radius:8px;background:#0F1012;border:1px solid var(--line);user-select:all">{dialog.cmd}</code>
  </Modal>
{:else if dialog?.kind === 'share'}
  <ShareDialog path={folder} {canShare} onclose={() => (dialog = null)} />
{:else if dialog?.kind === 'versions'}
  <Versions {path} {canEdit} onclose={() => (dialog = null)} onchange={() => rev++} />
{:else if dialog?.kind === 'rename'}
  <Modal title="Rename" onclose={() => (dialog = null)}>
    <form onsubmit={(e) => { e.preventDefault(); rename(nameInput.trim()); }} style="display:flex;flex-direction:column;gap:14px">
      <label class="lbl">New name<input class="field" bind:value={nameInput} required /></label>
      <div class="row" style="justify-content:flex-end"><button type="button" class="btn" onclick={() => (dialog = null)}>Cancel</button><button class="btn pri">Rename</button></div>
    </form>
  </Modal>
{:else if dialog?.kind === 'move'}
  <Modal title="Move {f.name}" onclose={() => (dialog = null)}>
    <form onsubmit={(e) => { e.preventDefault(); move(destInput.trim()); }} style="display:flex;flex-direction:column;gap:14px">
      <label class="lbl">To folder<input class="field mono" bind:value={destInput} required /></label>
      <p class="note" style="margin:0">You need Editor on the folder it goes to.</p>
      <div class="row" style="justify-content:flex-end"><button type="button" class="btn" onclick={() => (dialog = null)}>Cancel</button><button class="btn pri">Move</button></div>
    </form>
  </Modal>
{:else if dialog?.kind === 'delete'}
  <Modal title="Move {f.name} to trash?" onclose={() => (dialog = null)}>
    <p style="margin:0;font-size:13px;line-height:1.55;color:var(--text-2)">It stays in your trash for {session.trash_days || 30} days, with its versions, and you can restore it from there.</p>
    <div class="row" style="justify-content:flex-end"><button class="btn" onclick={() => (dialog = null)}>Cancel</button><button class="btn pri" onclick={remove}>Move to trash</button></div>
  </Modal>
{/if}

<style>
  .viewer { gap: 18px; }
  .vhead { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  .vtitle { flex: 1 1 320px; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
  .trail { display: flex; align-items: center; gap: 6px; font-size: 12.5px; flex-wrap: wrap; }
  .trail a { color: var(--muted); }
  .trail a:hover { color: var(--text); }
  .fname { font-size: 22px; font-weight: 600; letter-spacing: -0.02em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
  .ext { flex: none; height: 26px; min-width: 40px; padding: 0 8px; border-radius: 6px; display: inline-flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 500; letter-spacing: .04em; text-transform: uppercase; }
  .vbody { display: flex; gap: 16px; align-items: flex-start; flex-wrap: wrap; }
  .stage { flex: 999 1 560px; min-width: 0; max-height: calc(100vh - 150px); min-height: 280px; overflow: hidden; display: flex; flex-direction: column; }
  .stage.fixed { height: calc(100vh - 150px); min-height: 420px; }
  .stage iframe { flex: 1; width: 100%; border: 0; background: #fff; }
  .stage.paper { background: #F7F5F0; border-color: #F7F5F0; }
  .imgwrap { flex: 1; display: grid; place-items: center; padding: 24px; overflow: auto;
    background-color: #0F1012; background-image: linear-gradient(#17191C 1px, transparent 1px), linear-gradient(90deg, #17191C 1px, transparent 1px); background-size: 20px 20px; }
  .imgwrap img { max-width: 100%; max-height: 100%; object-fit: contain; border-radius: 4px; box-shadow: 0 10px 40px rgba(0,0,0,.4); }
  .ph { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 14px; }
  .big { font-size: 40px; font-weight: 500; letter-spacing: .04em; }
  .codeview { flex: 1; overflow: auto; background: #0F1012; }
  .codeview table { border-collapse: collapse; font: 12.5px/1.65 var(--mono); width: 100%; }
  .codeview .ln, .csvt .ln { width: 1%; padding: 0 14px 0 18px; text-align: right; color: #4A4F57; user-select: none; vertical-align: top; white-space: nowrap; }
  .codeview .lc { padding: 0 18px 0 6px; color: var(--text-2); white-space: pre-wrap; word-break: break-word; }
  .codeview tr:first-child td { padding-top: 14px; }
  .csvwrap { flex: 1; overflow: auto; }
  .csvt { border-collapse: collapse; font-size: 12.5px; min-width: 100%; }
  .csvt th { position: sticky; top: 0; background: var(--card-2); text-align: left; font-weight: 500; color: var(--text); }
  .csvt th, .csvt td { padding: 8px 14px; border-bottom: 1px solid var(--line-soft); white-space: nowrap; }
  .csvt td { color: var(--text-2); font-variant-numeric: tabular-nums; }
  .csvt td.num, .csvt th.num { text-align: right; }
  .csvt .ln { vertical-align: middle; font: 11px var(--mono); }
  .md { flex: 1; overflow: auto; padding: 48px 56px 64px; color: #24262B; font-size: 15px; line-height: 1.7; }
  .md :global(> *) { max-width: 760px; margin-left: auto; margin-right: auto; }
  .md :global(h1) { font-size: 30px; line-height: 1.2; letter-spacing: -0.02em; margin: 0 auto 18px; color: #121317; }
  .md :global(h2) { font-size: 21px; letter-spacing: -0.01em; margin: 32px auto 10px; color: #121317; }
  .md :global(h3) { font-size: 16.5px; margin: 24px auto 8px; color: #121317; }
  .md :global(p) { margin: 0 auto 14px; }
  .md :global(ul), .md :global(ol) { padding-left: 22px; margin: 0 auto 14px; }
  .md :global(li) { margin: 4px 0; }
  .md :global(li.task) { list-style: none; margin-left: -22px; display: flex; gap: 10px; align-items: baseline; }
  .md :global(.md-box) { flex: none; width: 13px; height: 13px; border: 1.5px solid #9A958A; border-radius: 3px; transform: translateY(2px); }
  .md :global(.md-box.on) { background: #B87A1C; border-color: #B87A1C; }
  .md :global(a) { color: #A8661A; text-decoration: underline; text-underline-offset: 2px; }
  .md :global(code) { font: 13px var(--mono); background: #EAE6DC; color: #3A3428; border: 0; padding: 1px 5px; border-radius: 4px; }
  .md :global(pre) { background: #1A1B1F; color: #DADCE0; padding: 16px 18px; border-radius: 10px; overflow: auto; margin: 0 auto 16px; }
  .md :global(pre code) { background: none; padding: 0; color: inherit; font-size: 12.5px; line-height: 1.6; }
  .md :global(blockquote) { border-left: 3px solid #D9B26F; margin: 0 auto 14px; padding: 2px 0 2px 16px; color: #555; }
  .md :global(hr) { border: 0; border-top: 1px solid #DDD8CC; margin: 28px auto; }
  .md :global(table) { border-collapse: collapse; margin: 0 auto 16px; font-size: 13.5px; display: block; overflow-x: auto; }
  .md :global(th), .md :global(td) { border: 1px solid #DDD8CC; padding: 6px 12px; text-align: left; }
  .md :global(th) { background: #EFEBE2; }
  .md :global(.md-img) { color: #888; font-style: italic; }
  .rail { flex: 1 1 280px; max-width: 320px; min-width: 0; padding: 8px 18px 16px; display: flex; flex-direction: column; position: sticky; top: 20px; }
  .rail .kv { font-size: 12.5px; }
  .brk { text-align: right; min-width: 0; overflow-wrap: anywhere; }
  .brk .crumb { white-space: nowrap; }
  .tagrow { display: flex; flex-wrap: wrap; gap: 4px; justify-content: flex-end; min-width: 0; flex: 1 1 auto; margin-left: 12px; }
  .tagrow .tag { max-width: 100%; white-space: normal; overflow-wrap: anywhere; text-align: left; }
  .vers { padding: 14px 0 10px; display: flex; flex-direction: column; gap: 4px; border-bottom: 1px solid var(--line-soft); }
  .ver { display: flex; align-items: center; gap: 10px; padding: 7px 0; }
  .vdot { flex: none; width: 8px; height: 8px; border-radius: 50%; border: 1.5px solid var(--line-hover); }
  .ver.cur .vdot { background: var(--amber); border-color: var(--amber); }
  .ell { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .lnk { all: unset; cursor: pointer; color: var(--amber); font-size: 12px; }
  .lnk:focus-visible { outline: 2px solid var(--amber); outline-offset: 2px; border-radius: 3px; }
  .acts { display: flex; flex-wrap: wrap; gap: 6px; padding: 14px 0 6px; }
  .acts :global(.btn) { flex: 1 1 auto; }
  @media (max-width: 1100px) { .rail { position: static; max-width: none; } .stage { height: 70vh; } .md { padding: 28px 22px 40px; } }
</style>
