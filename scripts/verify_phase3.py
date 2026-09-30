"""Run local + production browser checks in disposable databases; never read .env secrets."""
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[1]


def run_checks(public):
    with tempfile.TemporaryDirectory(prefix="autoqa-verification-", ignore_cleanup_errors=True) as directory:
        work = Path(directory)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {**os.environ, "PORT": str(port), "AUTOQA_DATA_DIR": str(work / "data"),
               "AUTOQA_ARTIFACT_DIR": str(work / "artifacts"), "DATABASE_URL": f"sqlite:///{work / 'studio.db'}",
               "AUTOQA_ENV": "production" if public else "test", "DEMO_MODE": str(public).lower(),
               "DEMO_LLM_ENABLED": "false", "DEMO_IP_LIMIT": "1", "DEMO_DAILY_LIMIT": "2",
               "LLM_PROVIDER": "mock", "AUTOQA_API_TOKEN": "", "AUTOQA_MCP_ENABLED": "false",
               "LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": "", "AUTOQA_PUBLIC_BASE_URL": "",
               "RAILWAY_PUBLIC_DOMAIN": "", "OPENAI_API_KEY": "", "DEEPSEEK_API_KEY": ""}
        ssl_options = ""
        scheme = "https" if public else "http"
        if public:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
            cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                    .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc))
                    .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1)).sign(key, hashes.SHA256()))
            (work / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            (work / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            env["VERIFY_KEY"], env["VERIFY_CERT"] = str(work / "key.pem"), str(work / "cert.pem")
            ssl_options = ", ssl_keyfile=os.environ['VERIFY_KEY'], ssl_certfile=os.environ['VERIFY_CERT']"
        base = f"{scheme}://127.0.0.1:{port}"
        env["AUTOQA_PUBLIC_BASE_URL"] = base
        code = ("import os, uvicorn; from autoqa.app import create_app; from autoqa.config import Settings; "
                "from autoqa.safe_logging import configure_logging; configure_logging(); "
                "s=Settings(_env_file=None); uvicorn.run(create_app(s),host='127.0.0.1',port=s.port,"
                "access_log=False,log_config=None" + ssl_options + ")")
        with (work / "server.log").open("w") as log:
            process = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, env=env, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            try:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError("Disposable verification server did not start")
                    try:
                        httpx.get(base + "/health", verify=False, timeout=1).raise_for_status()
                        break
                    except httpx.HTTPError:
                        time.sleep(0.2)
                else:
                    raise RuntimeError("Verification server timeout")
                script = "verify_public_demo.py" if public else "verify_studio.py"
                output = ROOT / "docs" / "assets" if public else ROOT / "work" / "phase3-studio"
                subprocess.run([sys.executable, str(ROOT / "scripts" / script), "--base", base,
                                "--output", str(output)], cwd=ROOT, check=True, env=env)
            finally:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                else:
                    process.terminate()
                process.wait(timeout=20)


if __name__ == "__main__":
    run_checks(False)
    run_checks(True)
