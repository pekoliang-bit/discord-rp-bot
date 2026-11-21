"""Render staging service: runs probes before HTTP readiness; never logs in to Discord."""
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from scripts.verify_turso_release import verify_release


class Handler(BaseHTTPRequestHandler):
    def _respond(self, include_body=True):
        if self.path.split('?', 1)[0] not in ('/', '/healthz', '/ready'):
            self.send_error(404)
            return
        body = b'TURSO_RELEASE_PROBES_OK; Discord not started\n'
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def do_GET(self):
        self._respond(include_body=True)

    def do_HEAD(self):
        self._respond(include_body=False)


if __name__ == '__main__':
    verify_release()
    print('TURSO_RELEASE_SERVICE_READY', flush=True)
    ThreadingHTTPServer(('0.0.0.0',int(os.getenv('PORT','10000'))),Handler).serve_forever()
