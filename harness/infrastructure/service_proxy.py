"""Fixed upstream relay for Docker Desktop internal networks. No workspace mount."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os


class Relay(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def forward(self):
        if not self.path.startswith('/') or self.path.startswith('//'):
            self.send_error(400, 'Only fixed upstream paths are supported')
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 <= length <= 4 * 1024 * 1024 or self.headers.get('Transfer-Encoding'):
                raise ValueError('Unsupported request body')
            body = self.rfile.read(length) if length else None
            connection = http.client.HTTPConnection(os.environ['UPSTREAM_HOST'],
                int(os.environ['UPSTREAM_PORT']), timeout=45)
            try:
                connection.request(self.command, self.path, body=body, headers={
                    'Content-Type': 'application/json', 'X-Workspace-Token': self.headers.get('X-Workspace-Token', '')})
                response = connection.getresponse()
                content = response.read(8 * 1024 * 1024 + 1)
                if len(content) > 8 * 1024 * 1024:
                    raise ValueError('Upstream response too large')
                self.send_response(response.status)
                self.send_header('Content-Type', response.getheader('Content-Type', 'application/json'))
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(content)
                self.close_connection = True
            finally:
                connection.close()
        except (OSError, ValueError, http.client.HTTPException):
            self.send_error(502, 'Registered upstream unavailable')

    def log_message(self, *_):
        pass  # Do not log credentials, paths or document request bodies.


if __name__ == '__main__':
    server = ThreadingHTTPServer(('0.0.0.0', 8300), Relay)
    server.daemon_threads = True
    server.serve_forever()
