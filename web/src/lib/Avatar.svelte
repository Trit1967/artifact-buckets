<script>
  import { initials, tint } from './format.js';
  let { name, size = 30, square = false } = $props();
  const c = $derived(tint(name));
</script>
<span class="avatar" style="width:{size}px;height:{size}px;background:{c[0]};color:{c[1]};font-size:{Math.round(size * 0.4)}px;border-radius:{square ? '8px' : '50%'}">{initials(name)}</span>
