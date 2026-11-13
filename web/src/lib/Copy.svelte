<script>
  import Icon from './Icon.svelte';
  let { text, label = '' } = $props();
  let done = $state(false);
  async function copy() {
    try { await navigator.clipboard.writeText(text); done = true; setTimeout(() => (done = false), 1500); } catch { /* clipboard blocked */ }
  }
</script>
<div class="cp">
  <code class="mono">{text}</code>
  <button class="btn sm" onclick={copy} aria-label="Copy {label || text}"><Icon name={done ? 'check' : 'copy'} size={14} />{done ? 'Copied' : 'Copy'}</button>
</div>
<style>
  .cp { display: flex; align-items: center; gap: 10px; padding: 8px 8px 8px 14px; border-radius: 10px; background: #0F1012; border: 1px solid var(--line); }
  code { flex: 1; min-width: 0; border: 0; background: none; padding: 0; font-size: 12.5px; line-height: 1.5; color: var(--text); white-space: normal; overflow-wrap: anywhere; }
</style>
