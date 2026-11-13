# Loopback stand-in for Claude Code's OAuth callback; appends each hit to /tmp/qa-retest/cb_hits.txt
import http.server, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        with open('/tmp/qa-retest/cb_hits.txt', 'a') as f: f.write(f'http://127.0.0.1:{self.server.server_port}{self.path}\n')
        self.send_response(200); self.send_header('Content-Type', 'text/plain'); self.end_headers(); self.wfile.write(b'callback ok')
    def log_message(self, *a): pass
http.server.HTTPServer(('127.0.0.1', int(sys.argv[1])), H).serve_forever()
