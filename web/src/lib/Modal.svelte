<script>
  import Icon from './Icon.svelte';
  let { title, onclose, children, width = 520, footer } = $props();
  let box;
  $effect(() => {
    const prev = document.activeElement;
    box?.querySelector('input, select, textarea, button.pri, button')?.focus();
    const onKey = (e) => { if (e.key === 'Escape') onclose?.(); };
    window.addEventListener('keydown', onKey);
    return () => { window.removeEventListener('keydown', onKey); prev?.focus?.(); };
  });
</script>

<div class="scrim" role="presentation" onclick={(e) => { if (e.target === e.currentTarget) onclose?.(); }}>
  <div class="dlg card" role="dialog" aria-modal="true" aria-label={title} bind:this={box} style="max-width:{width}px">
    <div class="hd">
      <h2>{title}</h2>
      <button class="btn icon sm" aria-label="Close" onclick={() => onclose?.()}><Icon name="x" /></button>
    </div>
    <div class="bd">{@render children()}</div>
    {#if footer}<div class="ft">{@render footer()}</div>{/if}
  </div>
</div>

<style>
  .scrim { position: fixed; inset: 0; background: rgba(5, 6, 8, .72); backdrop-filter: blur(3px); display: flex; align-items: flex-start;
    justify-content: center; padding: 10vh 16px 16px; z-index: 50; overflow-y: auto; animation: fade .12s ease-out; }
  .dlg { width: 100%; box-shadow: 0 30px 80px rgba(0, 0, 0, .5); animation: rise .16s ease-out; }
  .hd { display: flex; align-items: center; justify-content: space-between; padding: 18px 20px 6px; }
  .bd { padding: 12px 20px 20px; display: flex; flex-direction: column; gap: 14px; }
  .ft { padding: 14px 20px; border-top: 1px solid var(--line); display: flex; justify-content: flex-end; gap: 10px; }
  @keyframes fade { from { opacity: 0; } }
  @keyframes rise { from { transform: translateY(8px); opacity: 0; } }
</style>
