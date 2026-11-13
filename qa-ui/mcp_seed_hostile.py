"""Create hostile-looking data through the real APIs (as maya), so the UI can be checked for XSS and layout breakage."""
import json, os, sys, urllib.request, http.cookiejar
B = os.environ.get('BASE', 'http://localhost:8098')
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), urllib.request.ProxyHandler({}))
def req(method, url, body=None, headers=None):
    h = {'Content-Type': 'application/json', **(headers or {})}
    r = urllib.request.Request(B + url, data=json.dumps(body).encode() if body is not None else None, method=method, headers=h)
    try:
        with op.open(r) as resp: return resp.status, json.loads(resp.read() or b'null')
    except urllib.error.HTTPError as e: return e.code, e.read().decode()[:300]
s, d = req('POST', '/api/auth/login', {'username': 'maya', 'password': 'demo-password-123'})
csrf = d['csrf']
s, t = req('POST', '/api/tokens', {'name': 'qa hostile', 'scope': 'write', 'days': 1}, {'X-CSRF-Token': csrf})
tok = t['token']
n = 0
def mcp(tool, **args):
    global n; n += 1
    s, d = req('POST', '/mcp', {'jsonrpc': '2.0', 'id': n, 'method': 'tools/call', 'params': {'name': tool, 'arguments': args}},
               {'Authorization': 'Bearer ' + tok, 'Accept': 'application/json, text/event-stream'})
    res = d['result'] if isinstance(d, dict) and 'result' in d else d
    txt = res['content'][0]['text'] if isinstance(res, dict) and res.get('content') else str(res)
    print(f'{tool} {args.get("name") or args.get("path")!r:60.60} -> {"ERR " if isinstance(res, dict) and res.get("isError") else ""}{txt[:140]}')
    return res
X = '<img src=x onerror=window.__xss=7>'
names = ["x'onmouseover='window.__xss=3'.html", '{{constructor.constructor}}.md', '‮lmth.exe', 'a&amp;b &lt;tag&gt;.txt',
         'very-long-' + 'name-' * 36 + '.json', 'emoji-\U0001F680-report.md', 'javascript:alert(1).txt', 'tab\there.txt', 'nul\x00.txt',
         'CON.txt', 'semi;colon%2e%2e.txt', '..%2fescape.txt', 'back`tick$(id).sh']
for nm in names:
    mcp('save_artifact', name=nm, folder='/users/maya/inbox', content='hello ' + X)
mcp('save_artifact', name='meta-xss.md', folder='/users/maya/inbox', content='# meta', project=X, git_remote='javascript:alert(1)//' + X,
    git_commit=X, tags=[X, '"><script>window.__xss=8</script>', 'normal-tag'])
mcp('create_folder', path='/users/maya/inbox/' + "fold'er &amp; <x>")
mcp('create_folder', path='/users/maya/inbox/fold-er-' + 'deep-' * 30)
mcp('save_artifact', name='big-preview.txt', folder='/users/maya/inbox', content='A' * 20000 + '\n')
print('token', tok[:10])
open('/tmp/qa-ui/maya_pat', 'w').write(tok)
