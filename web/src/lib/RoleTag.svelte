<script>
  let { role } = $props();
  const cls = { owner: 'amber', editor: 'green', viewer: 'blue', admin: 'amber', auditor: 'violet', member: '' };
  const label = { owner: 'Owner', editor: 'Editor', viewer: 'Viewer', admin: 'Admin', auditor: 'Auditor', member: 'Member' };
</script>
<span class="tag {cls[role] ?? ''}">{label[role] ?? role}</span>
