"""Stage 2 Render web service, no Discord connection."""
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from scripts.verify_turso_schema import verify_schema, verify_ledger_rollback

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/healthz':
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type','text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write(b'TURSO_STAGE2_SCHEMA_OK; Discord not started\n')

if __name__ == '__main__':
    verify_schema()
    verify_ledger_rollback()
    print('TURSO_STAGE2_SERVICE_READY', flush=True)
    ThreadingHTTPServer(('0.0.0.0',int(os.getenv('PORT','10000'))),Handler).serve_forever()
