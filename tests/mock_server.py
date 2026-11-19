"""Tiny local OpenAI-compatible streaming server used by the tests."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):
        pass

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length))
        self.server.requests.append({"body": req, "auth": self.headers.get("Authorization")})
        if self.server.fail_status:
            self.send_error(self.server.fail_status)
            return
        pieces = self.server.pieces
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for piece in pieces:
            chunk = {"choices": [{"delta": {"content": piece}}]}
            self.wfile.write(("data: %s\n\n" % json.dumps(chunk)).encode())
            self.wfile.flush()
        usage = {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": len(pieces)}}
        self.wfile.write(("data: %s\n\n" % json.dumps(usage)).encode())
        self.wfile.write(b"data: [DONE]\n\n")


def start(pieces=("Hel", "lo ", "wor", "ld"), fail_status=0):
    """Start a server on a free port. Returns (server, base_url). Call server.shutdown() when done."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
    server.pieces = list(pieces)
    server.fail_status = fail_status
    server.requests = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]
