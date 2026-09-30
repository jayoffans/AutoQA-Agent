import gzip
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from langfuse import Langfuse
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from autoqa.telemetry import Telemetry


def test_real_langfuse_sdk_exports_redacted_observations(monkeypatch):
    received = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            received.append((self.path, body))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_):
            pass

    collector = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    thread = threading.Thread(target=collector.serve_forever, daemon=True)
    thread.start()
    client = Langfuse(public_key="pk-autoqa-local-test", secret_key="sk-autoqa-local-test",
                      base_url=f"http://127.0.0.1:{collector.server_port}")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-autoqa-local-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-autoqa-local-test")
    monkeypatch.setattr("langfuse.get_client", lambda: client)
    try:
        telemetry = Telemetry()
        with telemetry.span("autoqa-fixture", {"task_id": "local-test"}):
            with telemetry.span("browser-agent", {"action": "fill", "value": "fixture-private-value"}):
                pass
            with telemetry.span("planner-model", {"model": "fixture-model"}, generation=True) as span:
                span.update(output={"step_count": 2})
        telemetry.flush()
        assert received
        traces = [ExportTraceServiceRequest.FromString(body) for path, body in received
                  if path.endswith("/v1/traces")]
        spans = [s for trace in traces for resource in trace.resource_spans
                 for scope in resource.scope_spans for s in scope.spans]
        assert {"autoqa-fixture", "browser-agent", "planner-model"} <= {s.name for s in spans}
        assert b"fixture-private-value" not in b"".join(body for _, body in received)
        root = next(s for s in spans if s.name == "autoqa-fixture")
        assert all(s.parent_span_id == root.span_id for s in spans if s.name in {"browser-agent", "planner-model"})
    finally:
        client.shutdown()
        collector.shutdown()
        collector.server_close()
