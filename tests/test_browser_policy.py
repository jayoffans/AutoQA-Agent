import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from autoqa.browser import BrowserSession
from autoqa.config import Settings
from autoqa.security import TargetPolicy


async def test_redirect_is_blocked_before_contacting_unallowed_origin(tmp_path):
    contacts = []

    class Destination(BaseHTTPRequestHandler):
        def do_GET(self):
            contacts.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"forbidden")

        def log_message(self, *_):
            pass

    destination = ThreadingHTTPServer(("127.0.0.1", 0), Destination)

    class Source(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{destination.server_port}/forbidden")
            self.end_headers()

        def log_message(self, *_):
            pass

    source = ThreadingHTTPServer(("127.0.0.1", 0), Source)
    for server in (source, destination):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    settings = Settings(artifact_dir=tmp_path, allowed_origins=f"http://127.0.0.1:{source.server_port}")
    browser = BrowserSession(settings, TargetPolicy(settings), "redirect-test", 2000)
    try:
        try:
            await browser.start(f"http://127.0.0.1:{source.server_port}/redirect")
        except Exception:
            pass
        assert contacts == [], "Forbidden destination must never receive a request"
        assert browser.blocked and browser.blocked[0]["reason"] == "redirect_origin_policy"
    finally:
        await browser.close()
        for server in (source, destination):
            server.shutdown()
            server.server_close()
