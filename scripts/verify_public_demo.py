"""Browser acceptance against a disposable public Demo; writes real product screenshots."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def capture(page, path):
    page.locator("#toast").evaluate("element => element.hidden = true")
    page.screenshot(path=str(path), full_page=True, animations="disabled")


def verify(base, output):
    output.mkdir(parents=True, exist_ok=True)
    checks, errors = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base)
        expect(page.locator("#service-label")).to_have_text("All systems operational")
        expect(page.locator("#recent-runs")).to_contain_text("Authentication Regression Test")
        expect(page.locator("#mode-label")).to_have_text("Mock provider")
        capture(page, output / "dashboard.png")
        checks.append("Dashboard reads backend seed without credentials")
        page.locator('[data-view="projects"]').click()
        expect(page.locator("#project-list")).to_contain_text("Login API Quality Demo")
        expect(page.locator('[data-action="new-project"]')).to_be_hidden()
        capture(page, output / "projects.png")
        checks.append("Seed projects are visible and public project mutation is hidden")
        page.locator('[data-view="tasks"]').click()
        page.locator('[data-action="new-task"]:visible').first.click()
        expect(page.locator("#task-dialog")).to_be_visible()
        expect(page.locator("#test-type")).to_have_value("api")
        assert page.locator("#target").get_attribute("readonly") is not None
        page.locator("#run-button").click()
        expect(page.locator("#approval-box")).to_be_visible(timeout=30000)
        expect(page.locator("#plan-metrics")).to_contain_text("6")
        capture(page, output / "workflow.png")
        checks.append("Create limited Demo task and review six-case plan")
        page.locator('[data-approval="true"]').click()
        expect(page.locator("#approval-box")).to_be_hidden(timeout=30000)
        page.locator('[data-view="reports"]').click()
        expect(page.locator(".report-metrics")).to_contain_text("100%", timeout=30000)
        expect(page.locator(".bug-table")).to_contain_text("High")
        expect(page.locator(".report-metrics")).to_contain_text("5")
        expect(page.locator(".report-metrics")).to_contain_text("1")
        capture(page, output / "report.png")
        checks.append("Real HTTP execution produces 5 pass, 1 fail, 100% assertion execution and High finding")
        page.reload()
        expect(page.locator(".bug-table")).to_contain_text("High")
        checks.append("Workflow and report survive browser refresh")
        page.locator('[data-view="tasks"]').click()
        page.locator('[data-action="new-task"]:visible').first.click()
        page.locator("#run-button").click()
        expect(page.locator("#create-error")).to_be_visible()
        expect(page.locator("#create-error")).to_contain_text("额度")
        page.locator('[data-close="task-dialog"]').click()
        checks.append("Friendly daily rate-limit feedback in UI")
        for width in [390, 768, 1440]:
            page.set_viewport_size({"width": width, "height": 900})
            for view in ["dashboard", "projects", "tasks", "workflow", "reports"]:
                page.locator(f'[data-view="{view}"]').click()
                expect(page.locator(f"#{view}-view")).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (width, view)
        checks.append("Public pages fit mobile, tablet and desktop viewports")
        assert not errors, errors
        checks.append("No browser JavaScript errors")
        browser.close()
    result = {"passed": len(checks), "checks": checks, "javascript_errors": errors}
    (output / "public-ui-results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--output", type=Path, default=Path("work/public-qa"))
    args = parser.parse_args()
    verify(args.base, args.output)
