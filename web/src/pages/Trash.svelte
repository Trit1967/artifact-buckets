<script>
  import { api } from '../lib/api.js';
  import { session } from '../lib/session.svelte.js';
  import { toast, fail } from '../lib/toast.svelte.js';
  import { bytes, ago } from '../lib/format.js';
  import Icon from '../lib/Icon.svelte';

  let items = $state(null);
  async function load() { try { items = (await api.get('/api/trash')).items; } catch (e) { fail(e); } }
  load();
  async function restore(t) {
    try { const r = await api.post('/api/trash/restore', { id: t.id }); toast(`Restored to ${r.path || t.path}`); load(); } catch (e) { fail(e); }
  }
  const left = (iso) => Math.max(0, (session.trash_days || 30) - Math.floor((Date.now() - new Date(iso).getTime()) / 86400000));
</script>

<div class="page">
  <header class="page-head">
    <div><h1>Trash</h1><p class="sub">Deleted files and folders stay here for {session.trash_days || 30} days with their versions and sharing. {session.isAdmin ? 'As an admin you see everyone’s.' : ''}</p></div>
    <a class="btn" href="/files"><Icon name="chevL" />Back to files</a>
  </header>
  <section class="card">
    {#if !items}<div class="empty">Loading…</div>
    {:else if !items.length}<div class="empty">Nothing in the trash.</div>
    {:else}
      <div class="scroll-x"><table class="table">
        <thead><tr><th>Item</th><th>Was in</th>{#if session.isAdmin}<th>Deleted by</th>{/if}<th style="text-align:right">Size</th><th>Deleted</th><th>Kept for</th><th></th></tr></thead>
        <tbody>
          {#each items as t}
            <tr>
              <td><span class="row" style="gap:10px"><Icon name={t.is_dir ? 'folder' : 'list'} style="color:var(--muted)" /><span style="font-weight:500">{t.path.split('/').pop()}</span>{#if t.is_dir}<span class="note">{t.files} {t.files === 1 ? 'file' : 'files'}</span>{/if}</span></td>
              <td class="mono note">{t.path.split('/').slice(0, -1).join('/')}</td>
              {#if session.isAdmin}<td>{t.deleted_by_name || t.deleted_by}</td>{/if}
              <td class="mono" style="text-align:right">{bytes(t.size)}</td>
              <td class="muted">{ago(t.deleted_at)}</td>
              <td class="muted">{left(t.deleted_at)} days</td>
              <td style="text-align:right"><button class="btn sm" onclick={() => restore(t)}><Icon name="history" size={14} />Restore</button></td>
            </tr>
          {/each}
        </tbody>
      </table></div>
    {/if}
  </section>
</div>
