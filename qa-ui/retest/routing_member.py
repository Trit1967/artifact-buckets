"""UI-27: which routing rules does each member see?"""
import json, sys
sys.path.insert(0, '.')
exec(open('backend_retest.py').read().split('maya, mcsrf = login')[0])
adm, ac = login('admin', 'change-me-now-please')
s, b, _ = req(adm, 'GET', '/api/users')
for u in json.loads(b)['users']:
    if u['username'] == 'admin' or u['status'] != 'active': continue
    try: op, c = login(u['username'])
    except Exception as e: print(u['username'], 'login fail'); continue
    s, b, _ = req(op, 'GET', '/api/routing'); r = json.loads(b)
    print(f"{u['username']:10} role={u['system_role']:8} groups={u['groups']} sees={[x['target'] for x in r['rules']]} can_add={r['can_add']}")
