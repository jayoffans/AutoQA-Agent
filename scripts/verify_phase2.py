"""Verify a running Studio instance. Creates real projects, plans and HTTP test cases."""
import argparse
import json
import time
from pathlib import Path

import httpx

from autoqa.config import Settings


def verify(base, output):
    settings = Settings()
    headers = {"Authorization": "Bearer " + settings.api_token} if settings.api_token else {}
    with httpx.Client(base_url=base, headers=headers, timeout=20) as client:
        health = client.get("/api/health").raise_for_status().json()
        project = client.post("/api/projects", json={"name": "Phase 2 Login API acceptance",
            "description": "Generated plan with real HTTP execution", "url": base + "/demo/api/login"}).raise_for_status().json()
        created = client.post("/api/tasks", json={"project_id": project["id"], "test_type": "api",
            "target_url": base + "/demo/api/login", "goal": "Test all six login API contract scenarios"}).raise_for_status().json()
        task_id = created["id"]

        def wait(expected):
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                task = client.get(f"/api/tasks/{task_id}").raise_for_status().json()
                if task["status"] in expected:
                    return task
                time.sleep(0.25)
            raise RuntimeError("Acceptance task timed out")

        plan = wait({"awaiting_approval", "failed"})
        if plan["status"] == "failed":
            raise RuntimeError(json.dumps(plan["diagnosis"], ensure_ascii=False))
        assert len(plan["plan"]["cases"]) == 6
        client.post(f"/api/tasks/{task_id}/approve", json={"approved": True}).raise_for_status()
        final = wait({"passed", "failed"})
        assert len(final["steps"]) == 6
        assert final["run"]["coverage"] == 100
        assert final["run"]["passed"] == 5 and final["run"]["failed"] == 1, final["run"]
        assert final["findings"][0]["severity"] == "High"
        report = client.get(f"/api/tasks/{task_id}/report").raise_for_status().text
        model = None
        for line in client.get(f"/api/tasks/{task_id}/events").raise_for_status().text.splitlines():
            if line.startswith("data: "):
                event = json.loads(line[6:])
                if event["kind"] == "plan_created":
                    model = event["payload"].get("model")
        assert "autoqa123" not in json.dumps(final) and "autoqa123" not in report
        assert client.get(f"/api/projects/{project['id']}").status_code == 200
        output.mkdir(parents=True, exist_ok=True)
        (output / "report.html").write_text(report, encoding="utf-8")
        result = {"provider": health["llm_provider"], "model": model, "real_model_call": final["planner_mode"] == "llm",
            "project_id": project["id"], "task_id": task_id, "status": final["status"],
            "total_cases": 6, "passed": final["run"]["passed"], "failed": final["run"]["failed"],
            "coverage": final["run"]["coverage"], "findings": len(final["findings"]),
            "checks": ["project API", "provider generated plan", "human approval", "six real HTTP requests",
                       "persistent workflow", "failure evidence", "persistent report", "secret redaction"]}
        (output / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--output", type=Path, default=Path("work/phase2-acceptance"))
    args = parser.parse_args()
    verify(args.base, args.output)
