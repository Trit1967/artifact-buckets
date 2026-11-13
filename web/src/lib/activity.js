// Turn an audit event into a sentence for people, not a log line.
const name = (p) => (p || '').split('/').pop();
const who = (p) => String(p || '').split(':').slice(1).join(':') || p || '';
export function describe(e) {
  const d = e.detail || {};
  const t = e.target;
  switch (e.action) {
    case 'file.save': return e.ok ? [`saved`, name(t), `to ${t.split('/').slice(0, -1).join('/')}`] : [`was refused saving`, name(t), d.reason ? `(${d.reason})` : ''];
    case 'file.read': return ['opened', name(t), ''];
    case 'file.download': return ['downloaded', name(t), ''];
    case 'item.delete': return ['deleted', name(t), d.files > 1 ? `(${d.files} files)` : ''];
    case 'item.move': return ['moved', name(t), d.to ? `to ${d.to}` : ''];
    case 'item.restore': return ['restored', name(t), 'from the trash'];
    case 'file.restore_version': return [`restored version ${d.restored} of`, name(t), ''];
    case 'grant.set': return [`gave ${who(d.principal)} ${d.role || ''} on`, t, ''];
    case 'grant.remove': return [`removed ${who(d.principal)} from`, t, ''];
    case 'routing.add': return ['added a routing rule to', t, `for ${d.pattern}`];
    case 'routing.delete': return ['removed the routing rule to', t, ''];
    case 'oauth.deny': return ['declined to connect', t, ''];
    case 'directory.map_group': return ['started syncing AD group', t, ''];
    case 'volume.add': return ['added', t, 'to the storage pool'];
    case 'volume.drain': return ['started draining', t, ''];
    case 'volume.drained': return ['finished draining', t, ''];
    case 'volume.undrain': return ['stopped draining', t, ''];
    case 'storage.policy': return ['set where new files go to', t, ''];
    case 'smb.reconcile': return ['re-applied share permissions', '', `(${d.items} items)`];
    case 'folder.quota': case 'folder.settings': return ['changed settings on', t, ''];
    case 'trash.purge': return ['emptied old trash', '', `(${d.items} items)`];
    case 'folder.create': return ['created folder', t, ''];
    case 'signin.password': case 'signin.oidc': return [e.ok ? 'signed in' : 'failed to sign in', '', e.via === 'web' ? '' : `via ${e.via}`];
    case 'oauth.approve': return ['connected', t, ''];
    case 'oauth.token': return ['finished signing in', t, ''];
    case 'token.create': return ['created a personal token', t, ''];
    case 'token.revoke': return ['revoked', t, ''];
    case 'user.create': return ['added', t, d.role ? `as ${d.role}` : ''];
    case 'user.update': {
      if (d.status === 'suspended') return ['suspended', t, ''];
      if (d.status === 'active') return ['reactivated', t, ''];
      const parts = [];
      if (d.system_role) parts.push(`role → ${d.system_role}`);
      if (d.quota_bytes) parts.push(`quota → ${(d.quota_bytes / 1024 ** 3).toFixed(d.quota_bytes < 1024 ** 3 ? 2 : 0)} GB`);
      if (d.display_name) parts.push('name');
      return ['changed', t, parts.join(', ')];
    }
    case 'group.create': return ['created group', t, ''];
    case 'group.add_member': return [`added ${d.user} to`, t, ''];
    case 'group.remove_member': return [`removed ${d.user} from`, t, ''];
    case 'directory.sync': return ['synced the directory', '', `${d.users ?? 0} people, ${d.groups ?? 0} groups`];
    case 'ingest.pause': return ['paused Claude Code saves', '', ''];
    case 'ingest.resume': return ['resumed Claude Code saves', '', ''];
    case 'denied': return ['was denied', t, d.op ? `(${d.op})` : ''];
    default: return [e.action.replace(/[._]/g, ' '), t, ''];
  }
}
export const VIA = { mcp: 'Claude Code', web: 'Browser', api: 'API', system: 'System', smb: 'Windows share' };
