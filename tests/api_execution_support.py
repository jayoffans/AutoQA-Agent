"""Socket-only test target and exact pytest-local network exception. Never imported by production."""
import gzip
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

import autoqa.api_executor as executor_module
import autoqa.product_app as product_module
import autoqa.target_network as network


@pytest.fixture
def socket_api():
    state=SimpleNamespace(calls=[],mode='normal',release=threading.Event())
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass

        def do_POST(self):
            self.do_GET()

        def do_GET(self):
            body=self.rfile.read(int(self.headers.get('Content-Length',0)))
            state.calls.append({'method':self.command,'path':self.path,'host':self.headers['Host'],
                                'body':json.loads(body) if body else None})
            if state.mode=='timeout':
                state.release.wait(3)
            status=500 if state.mode=='500' else 302 if state.mode=='redirect' else 200
            payload=json.dumps({'data':{'success':True,'token':'socket-response-secret','id':1},
                                'echo':'socket-response-secret','request_echo':body.decode()}).encode()
            if state.mode in {'large','gzip','boundary'}:
                payload=b'x'*(256_000 if state.mode=='boundary' else 256_001)
            self.send_response(status)
            self.send_header('Content-Type','application/json')
            if state.mode=='redirect':
                self.send_header('Location','http://169.254.169.254/latest')
            if state.mode=='gzip':
                self.send_header('Content-Encoding','gzip')
                payload=gzip.compress(payload)
            self.send_header('Content-Length',str(len(payload)))
            self.end_headers()
            try:
                for start in range(0,len(payload),8192):
                    self.wfile.write(payload[start:start+8192])
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
                pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    state.url=f'http://127.0.0.1:{server.server_port}'
    state.server=server
    thread.start()
    try:
        yield state
    finally:
        state.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def allow_exact_test_target(monkeypatch,settings,url):
    assert settings.env=='test', 'Network exception can only be installed in a test fixture'
    allowed=urlsplit(url)
    original=network.resolve_target
    def resolver(target,allow_private=False):
        parsed=urlsplit(target)
        if (parsed.scheme,parsed.hostname,parsed.port)==(allowed.scheme,allowed.hostname,allowed.port):
            assert parsed.hostname=='127.0.0.1' and parsed.username is None and parsed.password is None
            return '127.0.0.1'
        return original(target,allow_private)
    monkeypatch.setattr(network,'resolve_target',resolver)
    monkeypatch.setattr(executor_module,'resolve_target',resolver)
    monkeypatch.setattr(product_module,'resolve_target',resolver)


@pytest.fixture
def tls_socket_api(socket_api,tmp_path):
    from tests.production_support import tls_files
    key,cert=tls_files(tmp_path/'untrusted-target')
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert,key)
    socket_api.server.socket=context.wrap_socket(socket_api.server.socket,server_side=True)
    socket_api.url=socket_api.url.replace('http:','https:',1)
    return socket_api
