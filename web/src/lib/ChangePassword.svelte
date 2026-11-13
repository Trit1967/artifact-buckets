<script>
  // Change your own password. Directory (AD or Microsoft) accounts change it there instead.
  import { api } from './api.js';
  import { session } from './session.svelte.js';
  import { toast } from './toast.svelte.js';
  import Modal from './Modal.svelte';

  let { onclose } = $props();
  let current = $state('');
  let next = $state('');
  let again = $state('');
  let busy = $state(false);
  let error = $state('');
  const local = session.user.source !== 'ad';
  const mismatch = $derived(again.length > 0 && next !== again);

  async function save(e) {
    e.preventDefault();
    if (next !== again) { error = 'The two new passwords don’t match.'; return; }
    busy = true; error = '';
    try {
      await api.post('/api/auth/password', { current, new: next });
      toast('Password changed');
      onclose();
    } catch (err) { error = err.message; } finally { busy = false; }
  }
</script>

<Modal title="Change your password" {onclose}>
  {#if !local}
    <p style="margin:0;font-size:13px;line-height:1.55;color:var(--text-2)">Your account comes from your organization’s directory, so change your password there. The new one works here straight away.</p>
    <div class="row" style="justify-content:flex-end"><button class="btn pri" onclick={onclose}>Done</button></div>
  {:else}
    <form onsubmit={save} style="display:flex;flex-direction:column;gap:14px">
      <label class="lbl">Current password<input class="field" type="password" autocomplete="current-password" bind:value={current} required /></label>
      <label class="lbl">New password<input class="field" type="password" autocomplete="new-password" minlength="12" bind:value={next} required /></label>
      <label class="lbl">New password again<input class="field" type="password" autocomplete="new-password" bind:value={again} required aria-invalid={mismatch} /></label>
      <p class="note" style="margin:0" class:bad={mismatch || error}>{error || (mismatch ? 'The two new passwords don’t match.' : 'At least 12 characters. A few unrelated words make a strong one.')}</p>
      <div class="row" style="justify-content:flex-end"><button type="button" class="btn" onclick={onclose}>Cancel</button><button class="btn pri" disabled={busy || mismatch}>Change password</button></div>
    </form>
  {/if}
</Modal>

<style>
  .bad { color: var(--red); }
</style>
