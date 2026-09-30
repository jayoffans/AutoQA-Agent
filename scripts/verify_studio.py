"""Verify the Studio UI against a running local service using real browser tasks.

Run against a disposable instance on 8765; this script creates real demo tasks.
"""
import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def capture(page, path):
    page.evaluate("document.activeElement?.blur(); window.scrollTo(0, 0)")
    page.locator("#toast").evaluate("element => element.hidden = true")
    page.locator(".sidebar nav").evaluate("element => element.scrollLeft = 0")
    page.screenshot(path=str(path), full_page=True, animations="disabled")


def verify(base: str, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    checks = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base)
        expect(page.get_by_role("heading", name="Dashboard", exact=False)).to_be_visible()
        expect(page.locator("#service-label")).to_have_text("All systems operational")
        expect(page.locator("#recent-runs")).to_contain_text("还没有运行记录")
        checks.append("Empty dashboard and service connection")
        capture(page, output / "studio-empty.png")

        page.locator('[data-action="demo"]').first.click()
        expect(page.get_by_role("dialog", name="创建测试任务")).to_be_visible()
        page.locator("#run-button").click()
        expect(page.locator("#approval-box")).to_be_visible(timeout=30000)
        expect(page.locator("#workflow-stages")).to_contain_text("Requirement Analyzer")
        expect(page.locator("#plan-metrics")).to_contain_text("Medium")
        assert page.locator(".stage.completed").count() == 2
        page.reload()
        expect(page.locator("#approval-box")).to_be_visible()
        expect(page.locator("#events")).to_contain_text("Test Planner")
        checks.append("Plan approval and persistent workflow survive browser refresh")
        capture(page, output / "studio-workflow.png")
        page.locator('[data-approval="true"]').click()
        expect(page.locator("#workflow-context .status")).to_have_text("Passed", timeout=40000)
        expect(page.locator("#screenshot-box img")).to_be_visible()
        assert page.locator(".stage.completed").count() == 4
        checks.append("Create regression, review real plan, approve, execute and capture evidence")

        page.locator("#view-report-button").click()
        expect(page.locator("#reports-view")).to_be_visible()
        expect(page.locator("#report-content")).to_contain_text("100%")
        expect(page.locator("#report-content")).to_contain_text("本次运行未发现问题")
        with page.expect_download() as download_info:
            page.locator("#report-button").click()
        download = download_info.value
        download.save_as(str(output / "studio-report.html"))
        assert "Test Report" in (output / "studio-report.html").read_text(encoding="utf-8")
        capture(page, output / "studio-report.png")
        checks.append("Passed assertions, coverage, HTML download")

        page.locator('[data-view="tasks"]').click()
        expect(page.locator("#tasks-view")).to_be_visible()
        page.locator('#tasks-view [data-action="new-task"]').click()
        page.locator('[data-template="network"]').click()
        page.locator("#run-button").click()
        expect(page.locator("#approval-box")).to_be_visible(timeout=30000)
        page.locator('[data-approval="true"]').click()
        expect(page.locator("#workflow-context .status")).to_have_text("Failed", timeout=30000)
        page.locator("#view-report-button").click()
        expect(page.locator("#reports-view")).to_be_visible()
        expect(page.locator(".bug-table")).to_contain_text("High")
        expect(page.locator(".bug-table")).to_contain_text("network_error")
        expect(page.locator("#report-content .metric.violet strong")).to_have_text("0%")
        capture(page, output / "studio-issues.png")
        checks.append("Network failure triage, unexecuted assertions, truthful coverage")

        page.locator('[data-view="projects"]').click()
        expect(page.locator("#projects-view")).to_be_visible()
        page.locator('[data-action="new-project"]').click()
        page.locator("#project-name").fill("QA Sandbox")
        page.locator("#project-url").fill(base + "/demo/form.html")
        page.locator('#project-form button[type="submit"]').click()
        expect(page.locator("#project-list")).to_contain_text("QA Sandbox")
        page.reload()
        expect(page.locator("#project-list")).to_contain_text("QA Sandbox")
        other = browser.new_page()
        other.goto(base + "#projects")
        expect(other.locator("#project-list")).to_contain_text("QA Sandbox")
        other.close()
        checks.append("Server project is visible in an independent browser session")
        page.get_by_role("article").filter(has_text="QA Sandbox").get_by_role("button", name="创建测试").click()
        page.locator("#test-type").select_option("smoke")
        page.locator("#goal").fill("检查页面主体可见")
        page.locator("#run-button").click()
        expect(page.locator("#workflow-context .status")).to_have_text("Passed", timeout=30000)
        expect(page.locator("#detail-id")).to_contain_text("structured")
        checks.append("Server projects persist and smoke uses structured API steps")

        page.locator('[data-view="tasks"]').click()
        expect(page.locator("#tasks-view")).to_be_visible()
        page.locator('#tasks-view [data-action="new-task"]').click()
        page.locator('[data-template="api"]').click()
        page.locator("#run-button").click()
        expect(page.locator("#approval-box")).to_be_visible(timeout=30000)
        page.locator('[data-approval="true"]').click()
        expect(page.locator("#workflow-context .status")).to_have_text("Failed", timeout=30000)
        page.locator("#view-report-button").click()
        expect(page.locator("#reports-view")).to_be_visible()
        expect(page.locator("#report-content .metric.green strong")).to_have_text("5")
        expect(page.locator("#report-content .metric.red strong")).to_have_text("1")
        expect(page.locator(".bug-table")).to_contain_text("api_contract_error")
        page.reload()
        expect(page.locator("#report-content .metric.violet strong")).to_have_text("100%")
        checks.append("API plan review, six real requests, failure report and refresh persistence")

        page.locator('[data-view="tasks"]').click()
        expect(page.locator("#tasks-view")).to_be_visible()
        page.locator("#task-search").fill("no-match-at-all")
        expect(page.locator("#task-list")).to_contain_text("没有匹配的任务")
        page.locator("#task-search").fill("")
        checks.append("Search and filtered empty state")

        page.route("**/api/tasks", lambda route: route.fulfill(status=401, json={"detail": "Unauthorized"}))
        page.locator('[data-action="refresh"]').first.click()
        expect(page.locator("#connection-error")).to_be_visible()
        expect(page.locator("#connection-error")).to_contain_text("访问令牌")
        page.unroute("**/api/tasks")
        page.locator('#connection-error [data-action="refresh"]').click()
        expect(page.locator("#connection-error")).to_be_hidden()
        checks.append("Connection error, authentication guidance and recovery")

        page.locator('[data-view="dashboard"]').click()
        expect(page.locator("#dashboard-view")).to_be_visible()
        expect(page.locator("#dashboard-metrics")).to_contain_text("Projects")
        page.locator("#toast").evaluate("element => element.hidden = true")
        capture(page, output / "studio-dashboard.png")
        for width in [390, 768, 1440]:
            page.set_viewport_size({"width": width, "height": 950})
            page.evaluate("document.fonts.ready")
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), width
            for view in ["dashboard", "tasks", "projects", "workflow", "reports", "settings"]:
                page.locator(f'[data-view="{view}"]').click()
                expect(page.locator(f"#{view}-view")).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (width, view)
            page.goto(base + "#dashboard")
            expect(page.locator("#dashboard-view")).to_be_visible()
            expect(page.locator("#dashboard-metrics")).to_contain_text("Projects")
            capture(page, output / f"studio-{width}.png")
        checks.append("All six pages at 390, 768 and 1440 px without viewport overflow")
        assert not errors, errors
        checks.append("No browser JavaScript errors")
        browser.close()
    result = {"checks": checks, "passed": len(checks), "javascript_errors": errors}
    (output / "studio-ui-results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--output", type=Path, default=Path("work/studio-qa"))
    args = parser.parse_args()
    verify(args.base, args.output)
