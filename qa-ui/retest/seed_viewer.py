"""Upload the viewer fixtures as maya through MCP (with tags/project so the rail has content)."""
import base64, os, sys
exec(open(os.path.join(os.path.dirname(__file__), 'backend_retest.py')).read().split('maya, mcsrf = login')[0])
maya, c = login('maya'); m = MCP(token_for(maya, c, 'viewer seed'))
d = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')
for fn in sorted(os.listdir(d)):
    data = open(os.path.join(d, fn), 'rb').read()
    ok, r = m('save_artifact', path=f'/users/maya/inbox/viewer/{fn}', content_base64=base64.b64encode(data).decode(),
              project='viewer-qa', git_remote='git@github.com:acme/viewer-qa.git', git_commit='0123456789abcdef',
              tags=['retest', 'a-very-long-tag-name-that-should-wrap-nicely-in-the-rail', 'x' * 40])
    print(fn, ok, (r.get('mime'), r.get('link')) if ok else r)
# a second and third version for the versions rail
for i in (2, 3):
    m('save_artifact', path='/users/maya/inbox/viewer/script.py', content=f'print("version {i}")\n')
# a big CSV (>500 rows) and big text (>2MB)
m('save_artifact', path='/users/maya/inbox/viewer/big.csv', content='a,b\n' + ''.join(f'{i},{i*2}\n' for i in range(800)))
m('save_artifact', path='/users/maya/inbox/viewer/huge.txt', content='y' * (2 * 1024 * 1024 + 10))
m('save_artifact', path='/users/maya/inbox/viewer/blob.bin', content_base64=base64.b64encode(bytes(range(256)) * 4).decode())
m('save_artifact', path='/users/maya/inbox/viewer/noext', content='plain text without extension\n')
m('save_artifact', path='/users/maya/inbox/viewer/bad.json', content='{"unterminated": ')
m('save_artifact', path='/users/maya/inbox/viewer/empty.md', content='')
