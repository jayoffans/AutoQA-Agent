import json
import zipfile
from io import BytesIO

import pytest
from conftest import create, wait_task


@pytest.mark.parametrize("page,goal,status,category", [
    ("login.html", "登录并完成多页面测试", "passed", None),
    ("broken.html", "诊断网络接口故障", "failed", "network_error"),
    ("console.html", "诊断前端异常和控制台", "failed", "frontend_error"),
    ("form.html", "运行错误断言", "failed", "assertion_error"),
    ("form.html", "定位失败和选择器诊断", "failed", "element_error"),
])
def test_real_browser_flows(client, page, goal, status, category):
    task_id = create(client, page, goal, step_timeout_ms=800, max_retries=0)
    task = wait_task(client, task_id)
    assert task["status"] == status, task
    assert task["planner_mode"] == "demo"
    assert task["steps"], task
    if category:
        assert task["diagnosis"]["category"] == category
        assert task["diagnosis"]["evidence"]
        assert task["diagnosis"]["root_cause_confirmed"] is False
    else:
        assert len(task["steps"]) == 8
        assert all(s["ok"] for s in task["steps"])
    image = task["steps"][-1]["artifacts"]["screenshot"]
    assert client.get(f"/api/tasks/{task_id}/artifacts/{image}").content.startswith(b"\x89PNG")
    trace = client.get(f"/api/tasks/{task_id}/artifacts/trace.zip")
    # Completion status can precede final trace flush by a small interval.
    import time
    for _ in range(30):
        if trace.status_code == 200:
            break
        time.sleep(0.1)
        trace = client.get(f"/api/tasks/{task_id}/artifacts/trace.zip")
    assert trace.status_code == 200
    with zipfile.ZipFile(BytesIO(trace.content)) as z:
        assert any(name.endswith(".trace") for name in z.namelist())
    assert "[REDACTED]" in json.dumps(task) if page == "login.html" else True


def test_retry_only_read_step(client):
    task_id = create(client, "delayed.html", "延迟渲染与重试", step_timeout_ms=500, max_retries=3)
    task = wait_task(client, task_id)
    assert task["status"] == "passed", task
    assert any(s["attempt"] > 0 for s in task["steps"]), task
    assert not any(s["attempt"] > 0 and s["step"]["action"] == "click" for s in task["steps"])


@pytest.mark.parametrize("approved,expected", [(True, "passed"), (False, "cancelled")])
def test_step_approval(client, approved, expected):
    task_id = create(client, "form.html", "填写表单并审批提交")
    task = wait_task(client, task_id)
    assert task["status"] == "awaiting_approval"
    assert task["approval"]["scope"] == "step"
    assert not any(s["step"]["action"] == "click" for s in task["steps"])
    r = client.post(f"/api/tasks/{task_id}/approve", json={"approved": approved})
    assert r.status_code == 202
    task = wait_task(client, task_id, expected)
    assert task["status"] == expected
    assert len([s for s in task["steps"] if s["step"]["action"] == "click"]) == int(approved)


def test_plan_checkpoint_survives_process_restart(client, server):
    task_id = create(client, "login.html", "登录并完成多页面测试", auto_approve_demo=False)
    task = wait_task(client, task_id)
    assert task["approval"]["scope"] == "plan"
    assert task["steps"] == []
    server.restart()
    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["status"] == "awaiting_approval"
    assert client.post(f"/api/tasks/{task_id}/approve", json={"approved": True}).status_code == 202
    task = wait_task(client, task_id, "passed")
    assert all(s["ok"] for s in task["steps"])


def test_step_checkpoint_does_not_replay_mutations_after_restart(client, server):
    task_id = create(client, "form.html", "填写表单并审批")
    task = wait_task(client, task_id)
    assert task["approval"]["scope"] == "step"
    before = len(task["steps"])
    server.restart()
    r = client.post(f"/api/tasks/{task_id}/approve", json={"approved": True})
    assert r.status_code == 409
    assert len(client.get(f"/api/tasks/{task_id}").json()["steps"]) == before
    assert client.post(f"/api/tasks/{task_id}/cancel").status_code == 200


def test_cancel_and_timeout(client):
    steps = [{"action": "assert_text", "value": "never appears", "description": "等待不存在文本"}]
    task_id = create(client, "form.html", "取消测试", steps=steps, step_timeout_ms=10000)
    assert client.post(f"/api/tasks/{task_id}/cancel").status_code == 200
    assert wait_task(client, task_id)["status"] == "cancelled"
    timed_id = create(client, "form.html", "超时测试", steps=steps,
                      step_timeout_ms=10000, task_timeout_seconds=5)
    timed = wait_task(client, timed_id)
    assert timed["status"] == "failed"
    assert timed["diagnosis"]["category"] == "task_timeout"


def test_sse_report_and_artifact_isolation(client):
    task_id = create(client, "login.html", "登录多页面测试")
    wait_task(client, task_id, "passed")
    events = client.get(f"/api/tasks/{task_id}/events")
    assert "event: update" in events.text
    assert "plan_created" in events.text
    assert "completed" in events.text
    assert "autoqa123" not in events.text
    assert client.get(f"/api/tasks/{task_id}/report?format=json").json()["id"] == task_id
    report = client.get(f"/api/tasks/{task_id}/report")
    assert report.status_code == 200 and "测试报告" in report.text
    assert client.get(f"/api/tasks/{task_id}/artifacts/..%2F.env").status_code == 404
    assert client.post(f"/api/tasks/{task_id}/approve", json={"approved": True}).status_code == 409
