from fastapi.testclient import TestClient
from starlette.requests import Request

from autoqa.app import create_app
from autoqa.config import Settings
from autoqa.demo_guard import source_address


def test_proxy_addresses_require_trusted_peer_and_single_valid_header():
    settings = Settings(_env_file=None, trusted_proxy_cidrs="100.64.0.0/24")

    def source(peer, headers):
        request = Request({"type": "http", "client": (peer, 1234), "headers": headers})
        return source_address(request, settings)

    assert source("100.64.0.1", [(b"x-real-ip", b"203.0.113.7")]) == "203.0.113.7"
    assert source("100.64.0.200", [(b"x-real-ip", b"203.0.113.7")]) == "203.0.113.7"
    assert source("198.51.100.1", [(b"x-real-ip", b"203.0.113.7")]) == "198.51.100.1"
    assert source("100.64.0.1", [(b"x-forwarded-for", b"203.0.113.7")]) == "100.64.0.1"
    assert source("100.64.0.1", [(b"x-real-ip", b"203.0.113.7, 203.0.113.8")]) == "100.64.0.1"
    assert source("100.64.0.1", [(b"x-real-ip", b"203.0.113.7"),
                                (b"x-real-ip", b"203.0.113.8")]) == "100.64.0.1"
    settings.trusted_proxy_cidrs = ""
    assert source("100.64.0.1", [(b"x-real-ip", b"203.0.113.7")]) == "100.64.0.1"


def test_quota_survives_rotating_trusted_gateways_cookies_and_restart(tmp_path):
    settings = Settings(_env_file=None, env="production", trusted_proxy_cidrs="100.64.0.0/24",
                        database_url=f"sqlite:///{tmp_path / 'demo.db'}", data_dir=tmp_path / "data",
                        artifact_dir=tmp_path / "artifacts", demo_ip_limit=3)
    payload = {"test_type": "api", "target_url": settings.studio_url + "/demo/api/login",
               "goal": "Check proxy source quota"}
    for index in range(4):
        with TestClient(create_app(settings), base_url="https://testserver",
                        client=(f"100.64.0.{index + 1}", 1234)) as client:
            response = client.post("/api/tasks", json=payload,
                                   headers={"X-Real-IP": "203.0.113.7", "X-Forwarded-For": f"192.0.2.{index}"})
            assert response.status_code == (202 if index < 3 else 429)
            if index == 3:
                assert response.headers["retry-after"] == "86400"
