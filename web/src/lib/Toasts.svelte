<script>
  import { toasts } from './toast.svelte.js';
  import Icon from './Icon.svelte';
</script>

<div class="stack" aria-live="polite">
  {#each toasts.items as t (t.id)}
    <div class="toast {t.kind}"><Icon name={t.kind === 'err' ? 'alert' : 'check'} />{t.text}</div>
  {/each}
</div>

<style>
  .stack { position: fixed; bottom: 22px; right: 22px; display: flex; flex-direction: column; gap: 10px; z-index: 60; max-width: min(420px, calc(100vw - 32px)); }
  .toast { display: flex; gap: 10px; align-items: flex-start; padding: 12px 14px; border-radius: 10px; background: #1A1C20; border: 1px solid var(--line-strong);
    font-size: 13px; line-height: 1.45; box-shadow: 0 12px 40px rgba(0, 0, 0, .45); animation: in .16s ease-out; }
  .toast.ok :global(.ic) { color: var(--green); margin-top: 1px; }
  .toast.err { border-color: #5A2C22; }
  .toast.err :global(.ic) { color: var(--red); margin-top: 1px; }
  @keyframes in { from { transform: translateY(6px); opacity: 0; } }
</style>
