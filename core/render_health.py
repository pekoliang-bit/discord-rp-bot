"""Render Web Service HTTP probe, independent of Discord's asyncio loop.

Endpoints:
  GET /         : lightweight service information (does not imply Discord readiness)
  GET /healthz  : 200 once Discord has connected; 503 before readiness/disconnect
  GET /ready    : alias of /healthz

Only run one HTTP listener per process. Uses Python standard library.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

LOG = logging.getLogger(__name__)
_ready = threading.Event()
_server: Optional[ThreadingHTTPServer] = None
_lock = threading.Lock()


def set_discord_ready(ready: bool) -> None:
    if ready:
        _ready.set()
    else:
        _ready.clear()


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split('?', 1)[0]
        if path == '/':
            code = 200
            result = {'service': 'discord-rp-bot', 'status': 'running', 'discord_ready': _ready.is_set()}
        elif path in ('/healthz', '/ready'):
            code = 200 if _ready.is_set() else 503
            result = {'status': 'ok' if code == 200 else 'starting', 'discord_ready': _ready.is_set()}
        else:
            code = 404
            result = {'error': 'not_found'}
        data = json.dumps(result, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: object) -> None:
        # Render checks the endpoint repeatedly; don't flood logs.
        if self.path.split('?', 1)[0] not in ('/', '/healthz', '/ready'):
            LOG.info('HTTP %s - %s', self.address_string(), fmt % args)


def start_health_server() -> ThreadingHTTPServer:
    global _server
    with _lock:
        if _server is not None:
            return _server
        try:
            port = int(os.environ.get('PORT', '10000'))
            if not 1 <= port <= 65535:
                raise ValueError('PORT must be between 1 and 65535')
        except ValueError as exc:
            raise RuntimeError('Invalid PORT environment variable') from exc
        server = ThreadingHTTPServer(('0.0.0.0', port), _Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, name='render-health', daemon=True)
        thread.start()
        _server = server
        LOG.info('Render health HTTP listening on 0.0.0.0:%s (GET /healthz)', port)
        return server


def stop_health_server() -> None:
    global _server
    with _lock:
        server = _server
        _server = None
    if server is not None:
        server.shutdown()
        server.server_close()
