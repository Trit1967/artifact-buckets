<script>
  import { api } from './api.js';
  import { toast, fail } from './toast.svelte.js';
  import Modal from './Modal.svelte';
  import { bytes, when, SOURCE } from './format.js';

  let { path, canEdit, onclose, onchange } = $props();
  let list = $state(null);
  async function load() { try { list = (await api.get('/api/fs/versions', { path })).versions; } catch (e) { fail(e); } }
  load();
  async function restore(v) {
    try {
      await api.post('/api/fs/restore-version', { path, version: v.version });
      toast(`Version ${v.version} is current again`);
      await load();
      onchange?.();
    } catch (e) { fail(e); }
  }
</script>

<Modal title="Versions of {path.split('/').pop()}" {onclose} width={620}>
  {#if !list}<div class="empty">Loading…</div>{:else}
    <div class="scroll-x"><table class="table">
      <thead><tr><th>Version</th><th>Saved</th><th>By</th><th>From</th><th style="text-align:right">Size</th><th></th></tr></thead>
      <tbody>
        {#each list as v}
          <tr>
            <td class="mono">v{v.version}{#if v.current} <span class="tag amber" style="margin-left:6px">Current</span>{/if}</td>
            <td style="white-space:nowrap">{when(v.saved_at || v.created_at)}</td>
            <td>{v.saved_by || '—'}</td>
            <td class="muted">{(SOURCE[v.source] || [v.source])[0]}</td>
            <td class="mono" style="text-align:right">{bytes(v.size)}</td>
            <td style="text-align:right">{#if !v.current && canEdit}<button class="btn sm" onclick={() => restore(v)}>Restore</button>{/if}</td>
          </tr>
        {/each}
      </tbody>
    </table></div>
    <p class="note" style="margin:0">Restoring copies the old version on top, so nothing is lost: the current one stays in this list.</p>
  {/if}
</Modal>
