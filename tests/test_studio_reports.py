from autoqa.reports import report_html, report_issues, report_metrics


def task(steps, plan, status="failed", diagnosis=None):
    return {"id": "abc12345-example", "status": status, "planner_mode": "demo",
            "request": {"goal": "<script>unsafe</script>", "target_url": "http://example.test"},
            "plan": {"steps": plan} if plan is not None else None, "steps": steps,
            "diagnosis": diagnosis, "created_at": "2026-09-30T12:00:00"}


def result(index, ok, attempt=0, action="assert_text"):
    return {"index": index, "ok": ok, "attempt": attempt, "duration_ms": 10,
            "step": {"action": action, "description": "check"}, "error": None if ok else "failure"}


def test_recovered_retry_is_one_passed_case_without_issue():
    data = task([result(0, False), result(0, True, 1)], [{"action": "assert_text", "description": "check"}],
                status="passed")
    assert report_metrics(data) == {"total": 1, "passed": 1, "failed": 0, "pending": 0, "coverage": 100}
    assert report_issues(data) == []
    output = report_html(data)
    assert "FAIL" in output and "PASS" in output  # Preserve attempts in evidence history.
    assert "<script>unsafe</script>" not in output
    assert "&lt;script&gt;" in output


def test_failure_before_assertions_does_not_invent_passes_or_coverage():
    data = task([result(0, False, action="click")],
                [{"action": "click", "description": "submit"},
                 {"action": "assert_text", "description": "check"}],
                diagnosis={"category": "network_error", "summary": "HTTP 500", "suggestion": "Check logs"})
    assert report_metrics(data) == {"total": 1, "passed": 0, "failed": 0, "pending": 1, "coverage": 0}
    assert report_issues(data)[0]["severity"] == "High"
    assert "NOT RUN" in report_html(data)


def test_planning_failure_has_runtime_issue_and_unknown_coverage():
    data = task([], None, diagnosis={"category": "agent_error", "summary": "Planning failed",
                                    "suggestion": "Check model connection"})
    assert report_metrics(data)["coverage"] is None
    assert report_issues(data)[0]["id"].startswith("RUN-")
    assert "Planning failed" in report_html(data)


def test_cancelled_partial_run_keeps_pending_cases():
    data = task([result(0, True)], [{"action": "assert_text", "description": "first"},
                                  {"action": "assert_url", "description": "second"}], status="cancelled")
    assert report_metrics(data) == {"total": 2, "passed": 1, "failed": 0, "pending": 1, "coverage": 50}
    assert report_issues(data) == []
