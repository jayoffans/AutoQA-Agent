import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

with socket.socket() as port_socket:
    port_socket.bind(("127.0.0.1", 0))
    TEST_PORT = int(os.getenv("AUTOQA_TEST_PORT", str(port_socket.getsockname()[1])))
BASE_URL = f"http://127.0.0.1:{TEST_PORT}"


class LiveServer:
    def __init__(self, directory: Path):
        self.directory = directory
        self.process = None
        self.logfile = None
        self.env = {**os.environ, "AUTOQA_DATA_DIR": str(directory / "data"),
                    "AUTOQA_ARTIFACT_DIR": str(directory / "artifacts"),
                    "AUTOQA_DATABASE_URL": f"sqlite:///{directory / 'tasks.db'}",
                    "AUTOQA_LLM_API_KEY": "", "AUTOQA_LLM_MODEL": "",
                    "AUTOQA_MCP_ENABLED": "false", "AUTOQA_API_TOKEN": "",
                    "DEMO_MODE": "false", "AUTOQA_SEED_DEMO": "false", "AUTOQA_ENV": "test",
                    "DATABASE_URL": f"sqlite:///{directory / 'tasks.db'}", "LLM_PROVIDER": "compatible",
                    "OPENAI_API_KEY": "", "DEEPSEEK_API_KEY": "", "LLM_MODEL": "",
                    "PORT": str(TEST_PORT), "AUTOQA_PUBLIC_BASE_URL": "", "RAILWAY_PUBLIC_DOMAIN": "",
                    "LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": ""}

    def start(self):
        self.logfile = (self.directory / "server.log").open("a", encoding="utf-8")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "autoqa.app:app", "--host", "127.0.0.1", "--port", str(TEST_PORT)],
            env=self.env, stdout=self.logfile, stderr=self.logfile,
        )
        end = time.monotonic() + 25
        while time.monotonic() < end:
            if self.process.poll() is not None:
                raise RuntimeError((self.directory / "server.log").read_text(encoding="utf-8"))
            try:
                if httpx.get(BASE_URL + "/api/health", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise RuntimeError("Test server failed to start")

    def stop(self, *, force=False):
        if self.process and self.process.poll() is None:
            if force:
                self.process.kill()
            else:
                self.process.terminate()
            self.process.wait(timeout=15)
        if self.logfile:
            self.logfile.close()

    def restart(self):
        # Checkpoint recovery tests simulate process failure. Windows terminate()
        # already kills immediately; use the same crash semantics on POSIX.
        self.stop(force=True)
        self.start()


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    try:
        httpx.get(BASE_URL + "/api/health", timeout=0.5)
    except httpx.HTTPError:
        pass
    else:
        pytest.fail("Port 8765 is occupied. Stop the local AutoQA server before running E2E tests.")
    live = LiveServer(tmp_path_factory.mktemp("autoqa-e2e"))
    live.start()
    yield live
    live.stop()


@pytest.fixture
def client(server):
    with httpx.Client(base_url=BASE_URL, timeout=20) as client:
        yield client


def wait_task(client, task_id, expected=None, timeout=35):
    final = {"passed", "failed", "cancelled", "interrupted", "awaiting_approval"}
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        r = client.get(f"/api/tasks/{task_id}")
        r.raise_for_status()
        task = r.json()
        if (task["status"] == expected if expected else task["status"] in final):
            return task
        time.sleep(0.15)
    raise AssertionError(f"Task did not finish: {task}")


def create(client, page, goal, **kwargs):
    r = client.post("/api/tasks", json={"target_url": BASE_URL + "/demo/" + page,
                                       "goal": goal, "auto_approve_demo": True, **kwargs})
    assert r.status_code == 202, r.text
    return r.json()["id"]
