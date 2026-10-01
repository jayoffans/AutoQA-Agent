"""Disposable production acceptance helpers. No real model credentials or calls."""
import base64
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[1]
PLAN = {"summary": "Compatibility fixture", "scope": "GET /users/1", "preconditions": [], "cases": [
    {"case_id": "COMPAT-1", "name": "Unicode 数据", "method": "GET", "path": "/users/1",
     "expected_status": 200, "expected": "Integer id", "response_schema": {
         "type": "object", "required": ["id"], "properties": {"id": {"type": "integer"}}}}]}


def random_key():
    value = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    mask_ci(value)
    return value


def mask_ci(*values):
    if os.getenv("GITHUB_ACTIONS") == "true":
        for value in values:
            if value:
                print("::add-mask::" + value, flush=True)


class PrivateContext(dict):
    def __repr__(self):
        return "<disposable production acceptance; credentials withheld>"


def clean_env(**values):
    # Neither host .env nor developer model/telemetry keys belong in acceptance services.
    env = {k: v for k, v in os.environ.items() if not k.startswith((
        "AUTOQA_", "MYSQL", "DEMO_", "OPENAI_", "DEEPSEEK_", "LANGFUSE_", "RAILWAY_"))
        and k not in {"DATABASE_URL", "LLM_PROVIDER", "LLM_MODEL", "PORT", "HOST"}}
    env.update({"LLM_PROVIDER": "mock", "PYTHONUTF8": "1", **{k: str(v) for k, v in values.items()}})
    return env


def checked_process(args, *, env=None, cwd=ROOT, timeout=120):
    result = subprocess.run(args, env=env, cwd=cwd, capture_output=True, timeout=timeout)
    if result.returncode:
        # Subprocess output can include interpolated DB URLs; never echo it into pytest or CI.
        raise RuntimeError(f"Acceptance subprocess failed (exit {result.returncode}; output withheld)")
    return result.stdout


class MockServices:
    """A real HTTP fixture for model responses and target API; never touches Demo."""
    def __init__(self, host="127.0.0.1"):
        self.calls = []
        calls = self.calls

        class Handler(BaseHTTPRequestHandler):
            def reply(self, value):
                body = json.dumps(value, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                calls.append(self.path)
                self.reply({"choices": [{"message": {"content": json.dumps(PLAN)}}]})

            def do_GET(self):
                calls.append(self.path)
                self.reply({"id": 1, "name": "生产兼容性"})

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer((host, 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def register(client, *, email=None, password=None, nickname="兼容用户"):
    email = email or secrets.token_hex(8) + "@example.com"
    password = password or secrets.token_urlsafe(24)
    response = client.post("/api/auth/register", json={"email": email, "password": password, "nickname": nickname})
    assert response.status_code == 201, "Registration failed"
    value = response.json()
    mask_ci(password, value["access_token"], value["refresh_token"], value["csrf_token"])
    client.headers["Authorization"] = "Bearer " + value["access_token"]
    client.headers["X-CSRF-Token"] = value["csrf_token"]
    return value, email, password


def create_project(client, target):
    response = client.post("/api/projects", json={"name": "持久项目 中文", "url": target, "project_type": "api"})
    assert response.status_code == 201
    return response.json()


def completed_task(client, target):
    item = create_project(client, target)
    model_key = secrets.token_urlsafe(24)
    mask_ci(model_key)
    assert client.put("/api/provider", json={"provider": "compatible", "base_url": target + "/v1",
        "api_key": model_key, "model": "mock-http-fixture"}).status_code == 200
    assert client.post(f'/api/projects/{item["id"]}/openapi', json={"endpoints": [
        {"method": "GET", "path": "/users/1", "summary": "Fixture", "parameters": [], "request_body": {},
         "responses": {"200": {"type": "object"}}}]}).status_code == 200
    response = client.post("/api/tasks", json={"project_id": item["id"], "goal": "Verify compatibility",
                                            "endpoints": ["GET /users/1"]})
    assert response.status_code == 202
    task_id = response.json()["id"]
    wait(client, task_id, "awaiting_approval")
    assert client.post(f"/api/tasks/{task_id}/approve", json={"approved": True}).status_code == 202
    task = wait(client, task_id, "passed")
    assert task["metrics"]["passed"] == 1
    assert client.get(f"/api/tasks/{task_id}/report").status_code == 200
    return item, task_id, model_key


def wait(client, task_id, expected):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = client.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200
        value = response.json()
        if value["status"] == expected:
            return value
        assert value["status"] != "system_error", "Fixture execution failed"
        time.sleep(0.05)
    raise AssertionError("Fixture task timed out")


def tls_files(directory):
    directory.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    instant = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(instant - timedelta(minutes=1))
        .not_valid_after(instant + timedelta(days=1)).add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False).sign(key, hashes.SHA256()))
    key_path, cert_path = directory / "key.pem", directory / "cert.pem"
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key_path, cert_path


class ProductionService:
    """Real production process over local TLS, with stable DB/key/dirs across restarts."""
    def __init__(self, settings, directory):
        self.directory, self.settings = directory, settings
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.url = f"https://localhost:{self.port}"
        self.env = clean_env(AUTOQA_MODE="production", AUTOQA_ENV="production",
            AUTOQA_SECRET_KEY=settings.secret_key, DATABASE_URL=settings.database_url,
            AUTOQA_DATA_DIR=settings.data_dir, AUTOQA_ARTIFACT_DIR=settings.artifact_dir,
            AUTOQA_ALLOW_PRIVATE_TARGETS="true", AUTOQA_PUBLIC_BASE_URL=self.url)
        self.key, self.cert = tls_files(directory)
        self.process = self.log = None

    def start(self):
        self.log = (self.directory / "service.log").open("ab")
        self.process = subprocess.Popen([sys.executable, "-m", "uvicorn", "autoqa.app:app", "--host", "127.0.0.1",
            "--port", str(self.port), "--ssl-keyfile", str(self.key), "--ssl-certfile", str(self.cert),
            "--no-access-log", "--log-level", "error"], cwd=self.directory, env=self.env,
            stdout=self.log, stderr=self.log)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            assert self.process.poll() is None, "Production service exited (logs withheld)"
            try:
                if httpx.get(self.url + "/health", verify=False, timeout=1).json() == {"status": "ok"}:
                    return self
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(0.1)
        self.stop()
        raise AssertionError("Production service failed to become healthy")

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=20)
        if self.log:
            self.log.close()

    def restart(self):
        self.stop()
        self.start()
