"""Reproducible real-browser benchmark. Start the app on port 8765 first."""
import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

CASES = [
    ("login-multipage", "login.html", "登录并完成多页面测试", "passed", None),
    ("network-500", "broken.html", "诊断网络接口故障", "failed", "network_error"),
    ("frontend-exception", "console.html", "检查前端异常和控制台", "failed", "frontend_error"),
    ("assertion-mismatch", "form.html", "运行错误断言", "failed", "assertion_error"),
    ("missing-element", "form.html", "定位失败和选择器诊断", "failed", "element_error"),
    ("retry-render", "delayed.html", "延迟渲染与重试", "passed", None),
    ("human-approval", "form.html", "填写表单并审批", "passed", None),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--output", default="docs/evaluation-results.json")
    args = parser.parse_args()
    headers = {"Authorization": "Bearer " + os.environ["AUTOQA_API_TOKEN"]} if os.getenv("AUTOQA_API_TOKEN") else {}
    rows = []
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=20) as client:
        for name, page, goal, expected, category in CASES:
            start = time.monotonic()
            request = {"target_url": args.base_url + "/demo/" + page, "goal": goal,
                       "auto_approve_demo": True, "step_timeout_ms": 500 if name == "retry-render" else 1000,
                       "max_retries": 3 if name == "retry-render" else 0}
            response = client.post("/api/tasks", json=request)
            response.raise_for_status()
            task_id = response.json()["id"]
            for _ in range(200):
                task = client.get(f"/api/tasks/{task_id}").json()
                if task["status"] == "awaiting_approval":
                    client.post(f"/api/tasks/{task_id}/approve", json={"approved": True}).raise_for_status()
                elif task["status"] in {"passed", "failed", "cancelled", "interrupted"}:
                    break
                time.sleep(0.1)
            observed_category = (task.get("diagnosis") or {}).get("category")
            rows.append({"case": name, "task_id": task_id, "expected_status": expected,
                         "observed_status": task["status"], "expected_category": category,
                         "observed_category": observed_category, "planner_mode": task["planner_mode"],
                         "correct": task["status"] == expected and observed_category == category,
                         "duration_seconds": round(time.monotonic() - start, 2),
                         "attempts": len(task["steps"]), "retries": sum(s["attempt"] > 0 for s in task["steps"])})
            print(f"{name}: {'OK' if rows[-1]['correct'] else 'MISMATCH'} ({task['status']})")
    result = {"recorded_at": datetime.now(timezone.utc).isoformat(), "suite": "controlled-demo-v1",
              "scope": "真实 Chromium + 演示模板；不代表开放网站或 LLM 泛化表现",
              "cases": rows, "correct": sum(r["correct"] for r in rows), "total": len(rows)}
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if not all(r["correct"] for r in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
